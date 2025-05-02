# PowerSave-TCP-Add-on

A containerized TCP proxy service for Home Assistant that detects incoming connections on specified ports (e.g., Minecraft, Emby, OtherService), triggers Wake-on-LAN to power on a remote worker machine, waits for it to become available, and seamlessly forwards traffic. This add-on is specifically designed for Home Assistant (HASSIO) environments, providing an efficient way to manage power consumption by keeping high-energy servers off until required.

## WORK IN PROGRESS

Esempio di configurazione in YAML:

```yaml
- listenport: 25565
  target_ip_proxy: "192.168.1.xx"
  target_port_proxy: 25565
  ha_url: IP
  ha_token: "TOKEN"
  ha_automation_id: automation.automationid1

- listenport: 8096
  target_ip_proxy: "192.168.1.xx"
  target_port_proxy: 8096
  ha_url: IP
  ha_token: "TOKEN"
  ha_automation_id: automation.automationid1/2/ect


```



### Commit a492f7d

Primi test effettuati:

- WSL con Ubuntu, esecuzione su computer portatile in WIFI
- Software per monitoraggio delle latenze: tcping

> TCPing instaura un nuovo socket TCP per ogni pacchetto, quindi il codice apre e chiude un socket in continuazione

Connessione diretta: Macchina2 -> Macchina1

| AVG: 8ms,7ms,8ms,9ms,11ms

Connessione attraverso proxy: Macchina1 -> Macchina2 -> Macchina1

| AVG: 57ms,42ms,48ms,36ms,39ms
