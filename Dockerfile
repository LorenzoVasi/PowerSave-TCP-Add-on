# Step 1: Usa una base leggera, come Alpine con Python
FROM python:3.11-alpine

# Step 2: Aggiorna l'immagine e installa dipendenze necessarie (es. socat per il proxy TCP)
RUN apk update && apk add --no-cache socat

# Step 3: Imposta la cartella di lavoro
WORKDIR /app

# Step 4: Copia il file requirements.txt (che conterrà le dipendenze Python)
COPY requirements.txt .

# Step 5: Installa le dipendenze Python
RUN pip install --no-cache-dir -r requirements.txt

# Step 6: Copia il codice dell'add-on nella directory di lavoro
COPY . .

# Step 7: Comando per eseguire il nostro script Python
CMD ["python", "main.py"]
