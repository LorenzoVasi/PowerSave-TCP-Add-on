import os
import time
import socket
import yaml
from wakeonlan import send_magic_packet
import threading
import requests
import logging
import selectors
import errno
import ipaddress
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

ha_call_config = {}
callback_conditions = {}
callback_results = {}
BUFFER_SIZE = 65536
ha_trigger_cache = {}
pending_clients = {}

# Mantieni traccia dei client attivi per porta
active_clients = {}

# Traccia l'IP del client, l'ultimo tempo di disconnessione, e se è in attesa di una nuova chiamata
client_disconnect_times = {}

CLIENT_TIMEOUT = 10  # Tempo massimo in secondi per non fare una nuova chiamata ad HA

def trigger_ha_async(port):
    def trigger():
        cfg = ha_call_config[port]
        try:
            logging.info(f"Sending HA trigger for port {port}")
            headers = {"Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/json"}
            payload = {"entity_id": cfg['automation']}
            url = f"{cfg['url']}/api/services/automation/trigger"
            response = requests.post(url, json=payload, headers=headers)
            if response.status_code != 200:
                raise Exception(f"Status {response.status_code}: {response.text}")
        except Exception as e:
            logging.error(f"Failed to trigger HA for port {port}: {e}")
            for sock in pending_clients.pop(port, []):
                try: sock.close()
                except: pass

    threading.Thread(target=trigger, daemon=True).start()

class PortProxy:
    def __init__(self, port, target_ip, target_port, allowed_regions=None):
        self.port = port
        self.target_ip = target_ip
        self.target_port = target_port
        self.sel = selectors.DefaultSelector()
        self.server = None
        self.allowed_regions = allowed_regions or []
        self.running = False
        self.is_target_ready = False
        active_clients[self.port] = []

    def start(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(('0.0.0.0', self.port))
        self.server.listen()
        self.server.setblocking(False)
        self.sel.register(self.server, selectors.EVENT_READ, self._accept)
        self.running = True
        threading.Thread(target=self._run_event_loop, daemon=True).start()
        logging.info(f"PortProxy listening on {self.port}")

    def _accept(self, sock):
        try:
            client_sock, addr = sock.accept()
        except Exception as e:
            logging.error(f"Accept error on port {self.port}: {e}")
            return

        ip = addr[0]
        if not self.is_ip_allowed(ip):
            logging.info(f"IP {ip} not allowed, closing connection")
            client_sock.close()
            return

        logging.info(f"Accepted {addr} on port {self.port}")
        client_sock.setblocking(False)

        # Verifica se questo client è recente (si riconnette entro il limite di tempo)
        if self._is_recently_disconnected(ip):
            logging.info(f"Client {ip} reconnected quickly, skipping HA trigger.")
            self._handle_proxy(client_sock)
        else:
            active_clients[self.port].append(client_sock)
            logging.info(f"Active clients on port {self.port}: {[c.getpeername() for c in active_clients[self.port] if c.fileno() != -1]}")
            pending_clients.setdefault(self.port, []).append(client_sock)
            if len(pending_clients[self.port]) == 1:
                callback_conditions[self.port] = threading.Condition()
                trigger_ha_async(self.port)
                threading.Thread(target=self._ha_callback_and_prepare, daemon=True).start()

    def _ha_callback_and_prepare(self):
        with callback_conditions[self.port]:
            logging.info(f"Waiting for HA callback for port {self.port}")
            callback_conditions[self.port].wait(timeout=90)
        if callback_results.get(self.port):
            logging.info(f"HA callback positive for port {self.port}")
            self._wait_target_ready()
        else:
            logging.warning(f"HA callback negative/timeout for port {self.port}")
            self._cleanup_pending_clients()

    def is_ip_allowed(self, ip):
        try:
            ip_obj = ipaddress.ip_address(ip)
            if ip_obj.is_private or ip_obj.is_loopback:
                return True
        except ValueError:
            logging.warning(f"Indirizzo IP non valido: {ip}")
            return False

        if not self.allowed_regions:
            return True

        try:
            r = requests.get(f'https://ipapi.co/{ip}/json/', timeout=2)
            region = r.json().get('region')
            if region in self.allowed_regions:
                return True
            else:
                logging.info(f"IP {ip} bloccato: regione '{region}' non ammessa")
                return False
        except Exception as e:
            logging.warning(f"Errore nella geolocalizzazione IP {ip}: {e}")
            return False

    def _wait_target_ready(self):
        def check():
            deadline = time.time() + 60
            while time.time() < deadline:
                try:
                    s = socket.create_connection((self.target_ip, self.target_port), timeout=2)
                    s.close()
                    self.is_target_ready = True
                    logging.info(f"Target ready on port {self.port}")
                    for c in pending_clients.pop(self.port, []):
                        self._handle_proxy(c)
                    return
                except Exception:
                    time.sleep(1)
            logging.error(f"Timeout waiting for target on port {self.port}")
            self._cleanup_pending_clients()

        threading.Thread(target=check, daemon=True).start()

    def _handle_proxy(self, client_sock):
        try:
            target_sock = socket.create_connection((self.target_ip, self.target_port))
            target_sock.setblocking(False)
            self.sel.register(client_sock, selectors.EVENT_READ, lambda sock: self._forward(sock, target_sock))
            self.sel.register(target_sock, selectors.EVENT_READ, lambda sock: self._forward(sock, client_sock))
            logging.info(f"Proxy established on port {self.port}")
        except Exception as e:
            logging.error(f"Failed to connect to target on port {self.port}: {e}")
            client_sock.close()

    def _forward(self, src_sock, dst_sock):
        try:
            data = src_sock.recv(BUFFER_SIZE)
            if data:
                dst_sock.sendall(data)
            else:
                logging.info(f"Socket closed by peer: {src_sock.getpeername()} on port {self.port}")
                self._cleanup(src_sock, dst_sock)
        except socket.error as e:
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                return
            logging.error(f"Forward error on port {self.port}: {e}")
            self._cleanup(src_sock, dst_sock)

    def _cleanup(self, sock1, sock2):
        peer_ips = []

        for s in (sock1, sock2):
            try:
                self.sel.unregister(s)
            except:
                pass
            try:
                peer_ip = s.getpeername()[0]
                peer_ips.append(peer_ip)
            except:
                pass
            try:
                s.close()
            except:
                pass
            if s in active_clients.get(self.port, []):
                active_clients[self.port].remove(s)

        # Salva i timestamp di disconnessione
        for ip in peer_ips:
            self._client_disconnected(ip)

        # Log della lista aggiornata di client attivi
        peers = []
        for c in active_clients[self.port]:
            try:
                peers.append(c.getpeername())
            except Exception:
                continue
        logging.info(f"Current active clients on port {self.port}: {peers}")


    def _cleanup_pending_clients(self):
        for sock in pending_clients.pop(self.port, []):
            try: sock.close()
            except: pass

    def _run_event_loop(self):
        while self.running:
            events = self.sel.select(timeout=1)
            for key, _ in events:
                callback = key.data
                callback(key.fileobj)

    def _is_recently_disconnected(self, ip):
        # Verifica se il client si è riconnesso entro il tempo limite
        current_time = time.time()
        last_disconnect_time = client_disconnect_times.get(ip, 0)
        if current_time - last_disconnect_time < CLIENT_TIMEOUT:
            return True
        return False

    def _client_disconnected(self, ip):
        client_disconnect_times[ip] = time.time()

class CallbackHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            data = json.loads(body)
            port = data.get("port")
            result = data.get("continue", False)

            if port in callback_conditions:
                with callback_conditions[port]:
                    callback_results[port] = result
                    callback_conditions[port].notify()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
                return

            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Porta non registrata")
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())


def load_config(filename='config.yaml'):
    base_path = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_path, filename)
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def start_callback_server(host='0.0.0.0', port=8080):
    server = HTTPServer((host, port), CallbackHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logging.info(f"Callback HTTP server started on {host}:{port}")


def main():
    cfgs = load_config()
    for c in cfgs:
        ha_call_config[c['listenport']] = {
            'url': c['ha_url'],
            'token': c['ha_token'],
            'automation': c['ha_automation_id']
        }

    proxies = []
    start_callback_server()
    for c in cfgs:
        p = PortProxy(
            c['listenport'],
            c['target_ip_proxy'],
            c['target_port_proxy'],
            allowed_regions=c.get('target_ip_regions')
        )
        p.start()
        proxies.append(p)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Server stopped.")


if __name__ == "__main__":
    main()
