# Connectivity Commands

Manage connections and data imports from external systems.

## Connection Commands

### List Connections

```bash
pltr connectivity connection list [--format FORMAT] [--output FILE]

# Example
pltr connectivity connection list --format json --output connections.json
```

### Get Connection Details

```bash
pltr connectivity connection get CONNECTION_RID [--format FORMAT]

# Example
pltr connectivity connection get ri.conn.main.connection.12345
```

### Create Connection

```bash
pltr connectivity connection create DISPLAY_NAME PARENT_FOLDER_RID [CONFIGURATION] [WORKER] [OPTIONS]

# Options:
#   --config-file TEXT    Path to JSON file with connection configuration
#   --worker-file TEXT    Path to JSON file with worker configuration

# Examples
pltr connectivity connection create "My Database" ri.compass.main.folder.xyz123 \
  '{"type": "jdbc"}' '{"workerType": "default"}'

# Using config files
pltr connectivity connection create "My Database" ri.compass.main.folder.xyz123 \
  --config-file connection-config.json --worker-file worker-config.json
```

### Get Connection Configuration

```bash
pltr connectivity connection get-config CONNECTION_RID [--format FORMAT]

# Example
pltr connectivity connection get-config ri.conn.main.connection.12345 --format json
```

### Update Connection Secrets

```bash
pltr connectivity connection update-secrets CONNECTION_RID --secrets-file FILE

# Secrets must be provided via file for security (avoids shell history exposure)

# Example (secrets.json: {"password": "secret123", "api_key": "abc..."})
pltr connectivity connection update-secrets ri.conn.main.connection.12345 \
  --secrets-file secrets.json
```

### Update Export Settings

```bash
pltr connectivity connection update-export-settings CONNECTION_RID [SETTINGS] [OPTIONS]

# Options:
#   --settings-file TEXT    Path to JSON file with export settings

# Examples
pltr connectivity connection update-export-settings ri.conn.main.connection.12345 \
  '{"enabled": true, "format": "parquet"}'

# Using settings file
pltr connectivity connection update-export-settings ri.conn.main.connection.12345 \
  --settings-file export-settings.json
```

### Upload JDBC Drivers

```bash
pltr connectivity connection upload-jdbc-drivers CONNECTION_RID DRIVER_FILES...

# Upload custom JAR files for JDBC connections

# Example
pltr connectivity connection upload-jdbc-drivers ri.conn.main.connection.12345 \
  driver.jar custom-driver-v2.jar
```

## File Import Commands

### List File Imports

```bash
pltr connectivity import list-file --connection CONNECTION_RID [--format FORMAT]

# Example
pltr connectivity import list-file --connection ri.conn.main.connection.123
```

### Get File Import Details

```bash
pltr connectivity import get-file IMPORT_RID --connection CONNECTION_RID [--format FORMAT]

# Example
pltr connectivity import get-file ri.import.main.file.12345 \
  --connection ri.conn.main.connection.123
```

## Table Import Commands

### List Table Imports

```bash
pltr connectivity import list-table --connection CONNECTION_RID [--format FORMAT]

# Example
pltr connectivity import list-table --connection ri.conn.main.connection.123
```

### Get Table Import Details

```bash
pltr connectivity import get-table IMPORT_RID --connection CONNECTION_RID [--format FORMAT]

# Example
pltr connectivity import get-table ri.import.main.table.12345 \
  --connection ri.conn.main.connection.123
```

### List all imports for a connection

```bash
CONNECTION="ri.conn.main.connection.123"

echo "File imports:"
pltr connectivity import list-file --connection $CONNECTION

echo "Table imports:"
pltr connectivity import list-table --connection $CONNECTION
```
