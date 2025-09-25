# Dual Provider Support per Sandbox API

## Panoramica

L'API sandbox ora supporta sia il provider **Daytona** che il provider **local_process**, permettendo di switchare tra ambienti cloud e locali senza modificare il codice dell'applicazione.

## Architettura

### Provider Supportati

1. **Daytona Provider** (`SANDBOX_PROVIDER=daytona`)
   - Utilizza sandbox remoti gestiti da Daytona
   - Workspace condiviso: `/workspace`
   - Richiede configurazione Daytona (API key, server URL, target)

2. **Local Process Provider** (`SANDBOX_PROVIDER=local_process`)
   - Utilizza il file system locale del worker
   - Workspace isolati per progetto: `/workspace/{project_id}`
   - Accesso diretto al file system tramite Path operations

### Configurazione

Configura il provider tramite variabile d'ambiente:

```bash
# Per usare Daytona
export SANDBOX_PROVIDER=daytona

# Per usare il provider locale
export SANDBOX_PROVIDER=local_process
```

### Path Management

Il sistema utilizza `WorkspaceConfig` per gestire i path in modo uniforme:

- **Daytona**: `/workspace` (workspace condiviso)
- **Local**: `/workspace/{project_id}` (workspace per progetto)

## Modifiche Implementate

### 1. API Unificata (`api.py`)

- **`get_sandbox_by_id_safely()`**: Rimossa dipendenza da `AsyncSandbox`, ora funziona con entrambi i provider
- **`normalize_path()`**: Aggiunto supporto per normalizzazione specifica del provider
- **`get_project_id_for_sandbox()`**: Helper function per ridurre duplicazione codice
- **Path handling**: Tutte le operazioni sui file ora utilizzano il workspace config appropriato

### 2. File Operations

Entrambi i provider implementano la stessa interfaccia FS:

```python
await sandbox.fs.upload_file(content, path)
await sandbox.fs.download_file(path)
await sandbox.fs.list_files(path)
await sandbox.fs.delete_file(path)
```

### 3. Compatibilità FileInfo

L'API gestisce le differenze tra le strutture `FileInfo` dei due provider:

- **Daytona**: Usa oggetti con attributi specifici
- **Local**: Usa `_FileInfo` dataclass con struttura semplificata

## Endpoints API

Tutti gli endpoint esistenti continuano a funzionare identicamente:

- `POST /sandboxes/{sandbox_id}/files` - Upload file
- `PUT /sandboxes/{sandbox_id}/files` - Update file
- `GET /sandboxes/{sandbox_id}/files` - List files
- `GET /sandboxes/{sandbox_id}/files/content` - Read file
- `DELETE /sandboxes/{sandbox_id}/files` - Delete file
- `DELETE /sandboxes/{sandbox_id}` - Delete sandbox
- `POST /project/{project_id}/sandbox/ensure-active` - Ensure sandbox active

### Nuovo Endpoint

- `GET /sandbox/status` - Restituisce informazioni sul provider attivo

## Test

Esegui il test per verificare il corretto funzionamento:

```bash
cd backend
python test_sandbox_api.py
```

Il test verifica:
- Creazione sandbox
- Operazioni file system (upload, download, list, delete)
- Configurazione workspace
- Normalizzazione path

## Vantaggi

1. **Flessibilità**: Switch tra cloud e locale senza modifiche al codice
2. **Performance**: Provider locale per development/testing veloce
3. **Scalabilità**: Provider Daytona per production
4. **Compatibilità**: API identica per entrambi i provider
5. **Isolamento**: Workspace separati per progetto nel provider locale

## Migrazione

Per migrare da un provider all'altro:

1. Cambia la variabile d'ambiente `SANDBOX_PROVIDER`
2. Riavvia l'applicazione
3. I nuovi sandbox useranno automaticamente il nuovo provider

I sandbox esistenti continueranno a funzionare fino alla loro naturale scadenza.

## Debug

Per debug, controlla i log per identificare quale provider è attivo:

```
INFO: Initialized sandbox API with database connection (provider: local_process)
INFO: [local_process] get_or_start test-project-123
```

Il provider attivo è sempre indicato nei log delle operazioni sandbox.
