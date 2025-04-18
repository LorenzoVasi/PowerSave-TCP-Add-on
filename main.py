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


#region "Machine Management" -------

def wake_on_lan(mac_address):
    print(f"Inviando pacchetto Wake-on-LAN a {mac_address}...")
    send_magic_packet(mac_address)

def shutdown_machine(mac_address):
    # Funzione per spegnere la macchina tramite WOL
    pass

#endregion ---------------------

#region "handle_traffic" -------

def start_tcp_proxy(listen_port, target_ip, target_port, mac_address):
    # Comando per reindirizzare il traffico TCP
    socat_command = f"socat TCP-LISTEN:{listen_port},fork TCP:{target_ip}:{target_port}"
    print(f"Avviando il proxy TCP su porta {listen_port}...")
    run(socat_command, shell=True)

def stop_proxy(listen_port):
    print(f"Chiudendo proxy su porta {listen_port}...")
    os.system(f"fuser -k {listen_port}/tcp")

#endregion ---------------------

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
    # Creiamo un socket per il server
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    # Bind per associare il socket a una porta e un indirizzo IP
    server_socket.bind(('0.0.0.0', listen_port))  # 0.0.0.0 permette di ascoltare tutte le interfacce
    server_socket.listen(5)  # Ascolta fino a 5 connessioni in attesa

    print(f"Server in ascolto sulla porta {listen_port}...")

    while True:
        # Iniziamo ad ascoltare la porta
        client_socket, client_address = server_socket.accept()  # Accetta una connessione in arrivo, è un blocco
        # Otteniamo l'indirizzo IP e la porta del client
        print(f"Connessione accettata da {client_address}")

        # Verifica se la connessione è già attiva
        if listen_port not in active_connections or not active_connections[listen_port]['active']:
            # Se la connessione non è attiva, avvia la macchina tramite WOL
            wake_on_lan(mac_address)
            threading.sleep(30)  # Attendi un attimo per dare tempo alla macchina di accendersi (in un futuro configurabile con YAML)
            start_tcp_proxy(listen_port, target_ip, target_port, mac_address)
            active_connections[listen_port] = {'active': True, 'last_activity': time.time()}

        # Aggiorna il tempo di attività ogni volta che arriva un pacchetto
        active_connections[listen_port]['last_activity'] = time.time()

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
