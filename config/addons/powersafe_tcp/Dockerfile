# Step 1: Usa una base leggera
FROM python:3.11-alpine

# Step 2: Aggiungi dipendenze di sistema essenziali in un singolo layer
RUN apk add --no-cache socat gcc musl-dev libffi-dev

# Step 3: Imposta la directory di lavoro
WORKDIR /app

# Step 4: Copia e installa i requirements prima per caching migliore
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Step 5: Copia il resto del codice
COPY . .

# Step 6: Esegui lo script
CMD ["python", "main.py"]
