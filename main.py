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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Config per porta e sincronizzazione HA
ha_call_config = {}

# Buffer size per proxy
BUFFER_SIZE = 4096

# Cache per trigger HA
ha_trigger_cache = {}  # {port: last_trigger_timestamp}
TRIGGER_TIMEOUT = 300  # secondi

# Cache connessioni multiple: {port: [client_sock]}
pending_clients = {}

class PortProxy:
    def __init__(self, port, target_ip, target_port):
        self.port = port
        self.target_ip = target_ip
        self.target_port = target_port
        self.sel = selectors.DefaultSelector()
        self.server = None
        self.running = False
        self.is_target_ready = False

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

        logging.info(f"Accepted {addr} on port {self.port}")
        client_sock.setblocking(False)

        if self.is_target_ready:
            self._handle_proxy(client_sock)
        else:
            pending_clients.setdefault(self.port, []).append(client_sock)
            if len(pending_clients[self.port]) == 1:
                self._trigger_ha_and_wait()

    def _trigger_ha_and_wait(self):
        cfg = ha_call_config[self.port]
        now = time.time()
        last_trigger = ha_trigger_cache.get(self.port, 0)
        if now - last_trigger < TRIGGER_TIMEOUT:
            logging.info(f"HA trigger recently done for port {self.port}, skipping...")
            self._wait_target_ready()
            return

        if not send_REST_API_and_wait(self.port, cfg['url'], cfg['token'], cfg['automation']):
            logging.error(f"HA call failed for port {self.port}")
            self._cleanup_pending_clients()
            return

        ha_trigger_cache[self.port] = now
        self._wait_target_ready()

    def _wait_target_ready(self):
        def check():
            for i in range(60):
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
                self._cleanup(src_sock, dst_sock)
        except socket.error as e:
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                return
            logging.error(f"Forward error on port {self.port}: {e}")
            self._cleanup(src_sock, dst_sock)

    def _cleanup(self, sock1, sock2):
        for s in (sock1, sock2):
            try: self.sel.unregister(s)
            except: pass
            try: s.close()
            except: pass

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


def send_REST_API_and_wait(port, ha_url, ha_token, ha_automation_id):
    logging.info(f"Triggering HA automation for port {port}")
    headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
    url = f"{ha_url}/api/services/automation/trigger"
    payload = {"entity_id": ha_automation_id}
    try:
        resp = requests.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            logging.error(f"HA call failed {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        logging.error(f"Exception during HA call: {e}")
        return False
    return True


def load_config(path='config.yaml'):
    with open(path) as f:
        return yaml.safe_load(f)


def main():
    cfgs = load_config()
    for c in cfgs:
        ha_call_config[c['listenport']] = {
            'url': c['ha_url'], 'token': c['ha_token'], 'automation': c['ha_automation_id']
        }
    proxies = []
    for c in cfgs:
        p = PortProxy(c['listenport'], c['target_ip_proxy'], c['target_port_proxy'])
        p.start()
        proxies.append(p)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
        for p in proxies:
            p.running = False

if __name__ == '__main__':
    main()