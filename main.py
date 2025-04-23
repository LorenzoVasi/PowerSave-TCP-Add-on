import os
import time
import socket
import psutil
import yaml
from wakeonlan import send_magic_packet
from subprocess import run
import threading
from subprocess import Popen

# Variabili globali per monitorare lo stato della connessione
active_connections = {}

# Funzione per caricare il file di configurazione YAML
def load_config(config_file):
    with open(config_file, 'r') as f:
        return yaml.safe_load(f)

def wake_on_lan(mac_address):
    send_magic_packet(mac_address, ip_address="192.168.1.255", port=9)

def shutdown_machine(mac_address):
    # Funzione per spegnere la macchina tramite WOL
    pass


def stop_proxy(listen_port):
    print(f"Chiudendo proxy su porta {listen_port}...")
    os.system(f"fuser -k {listen_port}/tcp")

def monitor_inactivity():
    while True:
        current_time = time.time()
        for listen_port, connection in active_connections.items():
            # Se la connessione è stata inattiva per più di 10 minuti, spegnila
            if current_time - connection['last_activity'] > 600:  # 600 secondi = 10 minuti
                print(f"Connessione sulla porta {listen_port} inattiva. Spegnimento della macchina.")
                # Chiudi il proxy e spegni la macchina
                stop_proxy(listen_port)
                shutdown_machine(connection['mac_address'])
                active_connections[listen_port]['active'] = False
        time.sleep(60)  # Verifica l'inattività ogni minuto

# Funzione per ascoltare la porta e gestire la connessione
def listen_for_connection(listen_port, mac_address, target_ip, target_port):
    def handle_client(client_socket):
        try:
            target_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            target_socket.connect((target_ip, target_port))
            print(f"[PROXY] Reindirizzazione stabilita con {target_ip}:{target_port}")
        except Exception as e:
            print(f"[ERROR] Connessione al target fallita: {e}")
            client_socket.close()
            return

        def forward(src, dst):
            try:
                while True:
                    data = src.recv(4096)
                    if not data:
                        break
                    try:
                        dst.sendall(data)
                    except (BrokenPipeError, OSError):
                        break
            except Exception as e:
                print(f"[FORWARD ERROR] {e}")
            finally:
                try:
                    src.shutdown(socket.SHUT_RDWR)
                except:
                    pass
                src.close()
                try:
                    dst.shutdown(socket.SHUT_RDWR)
                except:
                    pass
                dst.close()

        threading.Thread(target=forward, args=(client_socket, target_socket)).start()
        threading.Thread(target=forward, args=(target_socket, client_socket)).start()

    # Avvio socket per ascoltare
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(('0.0.0.0', listen_port))
    server_socket.listen(5)

    print(f"[LISTENER+PROXY] In ascolto su porta {listen_port}...")

    while True:
        client_socket, client_address = server_socket.accept()
        print(f"[LISTENER] Richiesta da {client_address}")

        if listen_port not in active_connections or not active_connections[listen_port]['active']:
            print(f"[WOL] Accendo la macchina {mac_address}...")
            wake_on_lan(mac_address)
            time.sleep(5)  # Aspetta un po' che si accenda

            active_connections[listen_port] = {'active': True, 'last_activity': time.time(), 'mac_address': mac_address}
        else:
            active_connections[listen_port]['last_activity'] = time.time()

        # Lancia la connessione proxy
        threading.Thread(target=handle_client, args=(client_socket,)).start()

def main():

    # Carica la configurazione dal file YAML
    try:
        config = load_config('config.yaml')
    except Exception as e:
        print(f"Errore nel caricare la configurazione: {e}")
        return 1
    
    for server in config:  # Itera su ogni configurazione (dizionario)
        listenport = server['listenport']
        mac_address = server['mac_address']
        target_ip = server['target_ip']
        target_port = server['target_port']
        
        # Verifica se la porta è già in uso
        if listenport in active_connections:
            print(f"Porta {listenport} già in uso.")
            continue

        # Avvia un thread separato per ogni configurazione
        listen_thread = threading.Thread(target=listen_for_connection, 
                                         args=(listenport, mac_address, target_ip, target_port))
        listen_thread.daemon = True  # Imposta il thread come daemon per terminare con il programma principale
        listen_thread.start()
        
    # Mantieni il main thread in esecuzione per non fermare il programma
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Interruzione del programma.")

if __name__ == "__main__":
    main()
