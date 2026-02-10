import gevent
from gevent import monkey
# Monkey patch for gevent
monkey.patch_all()

import os
import json
import glob
import socket
import logging  
import argparse
import subprocess 
import sys
import shutil
import re
import xmlrpc.client
import urllib.request
import urllib.error
import toml
from datetime import datetime
import uuid
import time
from flask import Flask, request, jsonify, send_from_directory, send_file
from richcolorlog import RichColorLogHandler
import zipfile
import io
import threading
from flask_socketio import SocketIO, emit, disconnect, join_room, leave_room
import paramiko
import atexit

__version__ = "2026.02.1"


def is_frozen():
    """Checks if the script is running in a frozen/packaged environment (e.g., PyInstaller)."""
    return getattr(sys, 'frozen', False)

def get_script_dir():
    """
    Determines the script's directory, handling both normal and frozen states.
    """
    if is_frozen():
        # For a frozen app, the base path is sys._MEIPASS, which contains the bundled files.
        return sys._MEIPASS
    else:
        # For a normal script, it's the directory of the __file__.
        return os.path.dirname(os.path.abspath(__file__))

# --- Command-Line Argument Parsing ---
parser = argparse.ArgumentParser(description='Flask server for the NHL LED Scoreboard Control Hub.')
parser.add_argument(
    '-d', '--scoreboard_dir', 
    default=None, 
    help='Path to the root of the nhl-led-scoreboard directory (where VERSION and plugins.json are located). Overrides config file.'
)
parser.add_argument(
    '--config',
    default=None,
    help='Path to the TOML configuration file. Defaults to config.toml in the script directory.'
)
# Debug Flag
parser.add_argument(
    '--debug',
    action='store_true',
    help='Run Flask in debug mode and show all pages for testing.'
)
parser.add_argument(
    '-v', '--version',
    action='version',
    version=f'%(prog)s {__version__}'
)
args = parser.parse_args()

# Get the directory the script itself is in
SCRIPT_DIR = get_script_dir()
# --- End Argument Parsing ---

# =============================================
# Logging Setup
# =============================================
# Set log level based on debug flag from args
log_level = logging.DEBUG if args.debug else logging.INFO

# Set up a generic logger for startup messages before Flask is initialized
handler = RichColorLogHandler(
    level=log_level,
    show_time=True,
    show_level=True,
    markup=True,
    show_background=False
)

logging.basicConfig(
    level=log_level,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[handler]
)



# --- Configuration ---
# Set default values
PORT = 8000
toml_config = {}

# If running as a frozen executable, sys.executable points to the app itself.
# We need to use a generic python interpreter to run other scripts like plugins.py.
# This can be overridden in config.toml if a specific python path is needed.
if is_frozen():
    # For a frozen app, assume 'python3' is available in the system's PATH.
    PYTHON_EXEC = 'python3'
else:
    # For development, use the same interpreter running this script to maintain venv.
    PYTHON_EXEC = sys.executable

SUPERVISOR_URL = '127.0.0.1'
SUPERVISOR_PORT = 9001
SCOREBOARD_DIR = '.'

# Determine config path: command line > default path
if args.config:
    CONFIG_TOML_PATH = args.config
else:
    CONFIG_TOML_PATH = os.path.join(SCRIPT_DIR, 'config.toml')

# Load from config.toml if it exists
if os.path.exists(CONFIG_TOML_PATH):
    try:
        with open(CONFIG_TOML_PATH, 'r') as f:
            toml_config = toml.load(f)
        logging.info(f"Successfully loaded configuration from [green]{CONFIG_TOML_PATH}[/green]")
    except Exception as e:
        logging.error(f"Failed to load configuration from [red]{CONFIG_TOML_PATH}[/red]: {e}")
        # Keep empty toml_config, defaults will be used
else:
    # Only log 'not found' if the default path was used
    if not args.config:
        logging.info(f"Using default configuration as {CONFIG_TOML_PATH} was not found.")
    else:
        logging.error(f"Specified config file not found at [red]{CONFIG_TOML_PATH}[/red].")
    
# Apply configurations from TOML file
PORT = toml_config.get('PORT', PORT)
PYTHON_EXEC = toml_config.get('PYTHON_EXEC', PYTHON_EXEC)
SUPERVISOR_URL = toml_config.get('SUPERVISOR_URL', SUPERVISOR_URL)
SUPERVISOR_PORT = toml_config.get('SUPERVISOR_PORT', SUPERVISOR_PORT)

# scoreboard_dir from config is used if the command-line arg is not provided
if args.scoreboard_dir is None:
    SCOREBOARD_DIR = toml_config.get('scoreboard_dir', SCOREBOARD_DIR)

# Command-line argument for scoreboard_dir takes highest precedence
if args.scoreboard_dir is not None:
    SCOREBOARD_DIR = args.scoreboard_dir

# Ensure SCOREBOARD_DIR is an absolute path
SCOREBOARD_DIR = os.path.abspath(SCOREBOARD_DIR)


# ASSETS_DIR is relative to the script's location
ASSETS_DIR = os.path.join(SCRIPT_DIR, 'static') 
# TEMPLATES_DIR is relative to the script's location
TEMPLATES_DIR = os.path.join(SCRIPT_DIR, 'templates') 


# Paths relative to --scoreboard_dir
CONFIG_DIR = os.path.join(SCOREBOARD_DIR, 'config')
CONFIG_FILE = 'config.json'
CONFIG_PATH = os.path.join(CONFIG_DIR, CONFIG_FILE)
VERSION_FILE = os.path.join(SCOREBOARD_DIR, 'VERSION')
PLUGINS_INDEX_FILE = os.path.join(SCOREBOARD_DIR, 'plugins_index.json')
PLUGINS_INSTALLED_FILE = os.path.join(SCOREBOARD_DIR, 'plugins.json')
PLUGINS_EXAMPLE_FILE = os.path.join(SCOREBOARD_DIR, 'plugins.json.example')
PLUGINS_LOCK_FILE = os.path.join(SCOREBOARD_DIR, 'plugins.lock.json')
PLUGINS_SCRIPT = os.path.join(SCOREBOARD_DIR, 'plugins.py')

# Absolute paths
SETUP_FILE = '/home/pi/.nhlledportal/SETUP'
# --- End Configuration ---

# --- Flask App Initialization ---
app = Flask(__name__, template_folder=TEMPLATES_DIR, static_folder=ASSETS_DIR)
app.config['SECRET_KEY'] = 'nhl-led-scoreboard-secret!'
socketio = SocketIO(app, async_mode='gevent')


# The root logger is configured by basicConfig.
# We set the levels for the Flask and Werkzeug loggers and let them propagate.
# Flask's default handler is not added because `has_level_handler` finds the root handler.
log = logging.getLogger('werkzeug')
log.setLevel(log_level)
app.logger.setLevel(log_level)
# --- End Flask App Initialization ---


# --- Helper Functions ---
def check_first_run():
    """Checks if the first-run SETUP file exists."""
    return os.path.exists(SETUP_FILE)

# =============================================
# MODIFIED: check_and_create_installed_plugins_file
# =============================================
def check_and_create_installed_plugins_file():
    """
    Checks for plugins.json. 
    Creates/Overwrites it from .example if:
    1. The file is missing.
    2. The file is invalid JSON.
    3. The file has no plugins (empty list).
    """
    should_restore = False
    
    if not os.path.exists(PLUGINS_INSTALLED_FILE):
        app.logger.warning(f"{PLUGINS_INSTALLED_FILE} not found.")
        should_restore = True
    else:
        try:
            with open(PLUGINS_INSTALLED_FILE, 'r') as f:
                content = f.read().strip()
                if not content:
                    # File is 0 bytes
                    app.logger.warning(f"{PLUGINS_INSTALLED_FILE} is empty.")
                    should_restore = True
                else:
                    # Parse JSON
                    data = json.loads(content)
                    # Check if 'plugins' key is missing or empty list
                    if not data.get('plugins'):
                        app.logger.info(f"{PLUGINS_INSTALLED_FILE} exists but has no plugins. Restoring defaults.")
                        should_restore = True
        except Exception as e:
            app.logger.warning(f"Error validating {PLUGINS_INSTALLED_FILE}: {e}. Will attempt restore.")
            should_restore = True

    if should_restore:
        if os.path.exists(PLUGINS_EXAMPLE_FILE):
            try:
                app.logger.info(f"Copying {PLUGINS_EXAMPLE_FILE} to {PLUGINS_INSTALLED_FILE}...")
                shutil.copy(PLUGINS_EXAMPLE_FILE, PLUGINS_INSTALLED_FILE)
                app.logger.info("Plugins file restored.")
            except Exception as e:
                app.logger.error(f"Failed to copy example plugins file: {e}")
        else:
            app.logger.warning(f"{PLUGINS_EXAMPLE_FILE} not found. Cannot create plugins.json.")
# =============================================

def get_version():
    """Reads the version from the VERSION file and prepends 'V' if missing."""
    try:
        with open(VERSION_FILE, 'r') as f:
            version = f.read().strip()
            if not version.upper().startswith('V'):
                version = f"V{version}"
            return version
    except FileNotFoundError:
        return "Unknown"
    except Exception as e:
        app.logger.error(f"Error reading {VERSION_FILE}: {e}")
        return "Error"

def get_builtin_boards():
    """
    Scans src/boards/builtins directory for plugin.json files and extracts board IDs.
    This finds all built-in boards that have configuration.
    """
    board_names = []
    
    # Define the builtin boards directory path
    boards_dir = os.path.join(SCOREBOARD_DIR, 'src', 'boards', "builtins")
    
    if not os.path.exists(boards_dir):
        app.logger.info(f"Boards directory not found at: {boards_dir}")
        return board_names
    
    app.logger.info(f"Scanning for builtin boards in: {boards_dir}")
    
    try:
        # Iterate over directories in boards_dir
        for item in os.listdir(boards_dir):
            board_path = os.path.join(boards_dir, item)
                
            if os.path.isdir(board_path):
                board_json_path = os.path.join(board_path, 'plugin.json')
                
                if os.path.exists(board_json_path):
                    try:
                        with open(board_json_path, 'r') as f:
                            data = json.load(f)
                            
                            # Check for 'boards' array
                            if 'boards' in data and isinstance(data['boards'], list):
                                for board in data['boards']:
                                    if 'id' in board:
                                        board_names.append(board['id'])
                    except json.JSONDecodeError:
                        app.logger.error(f"Invalid JSON in {board_json_path}")
                    except Exception as e:
                        app.logger.error(f"Error reading {board_json_path}: {e}")
                        
        app.logger.info(f"Loaded {len(board_names)} builtin boards: {board_names}")
        
    except Exception as e:
        app.logger.error(f"Error scanning builtin boards directory: {e}")

    return board_names

def get_plugin_boards():
    """
    Scans src/boards/plugins directory for plugins.json files and extracts board IDs.
    Skips 'example_board' directory.
    """
    board_names = []
    
    # Define the plugins directory path
    plugins_dir = os.path.join(SCOREBOARD_DIR, 'src', 'boards', 'plugins')
    
    if not os.path.exists(plugins_dir):
        app.logger.info(f"Plugins directory not found at: {plugins_dir}")
        return board_names

    app.logger.info(f"Scanning for plugins in: {plugins_dir}")

    # Walk through the directory structure
    # We only look one level deep for simplicity as per standard plugin structure, 
    # or we can walk. The user said "recurse through the directories", but 
    # typically these are in src/boards/plugins/<plugin_name>/plugins.json
    
    try:
        # iterate over directories in plugins_dir
        for item in os.listdir(plugins_dir):
            plugin_path = os.path.join(plugins_dir, item)
            
            # Skip example_board
            if item == 'example_board':
                continue
                
            if os.path.isdir(plugin_path):
                plugin_json_path = os.path.join(plugin_path, 'plugin.json')
                
                if os.path.exists(plugin_json_path):
                    try:
                        with open(plugin_json_path, 'r') as f:
                            data = json.load(f)
                            
                            # Check for 'boards' array
                            if 'boards' in data and isinstance(data['boards'], list):
                                for board in data['boards']:
                                    if 'id' in board:
                                        board_names.append(board['id'])
                    except json.JSONDecodeError:
                        app.logger.error(f"Invalid JSON in {plugin_json_path}")
                    except Exception as e:
                        app.logger.error(f"Error reading {plugin_json_path}: {e}")
                        
        app.logger.info(f"Loaded {len(board_names)} plugin boards: {board_names}")
        
    except Exception as e:
        app.logger.error(f"Error scanning plugins directory: {e}")

    return board_names

def check_supervisor():
    """Checks if the Supervisor web UI is running on its port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1) # 1 second timeout
    try:
        result = sock.connect_ex((SUPERVISOR_URL, SUPERVISOR_PORT))
        return result == 0
    except socket.error as e:
        app.logger.warning(f"Supervisor check failed: {e}")
        return False
    finally:
        sock.close()

def run_shell_script(command_list, timeout=120):
    """Helper function to run a generic shell script."""
    app.logger.info(f"Running shell command: {' '.join(command_list)} in [bold]{SCOREBOARD_DIR}[/bold]")
    try:
        process = subprocess.Popen(
            command_list, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            text=True, 
            encoding='utf-8',
            cwd=SCOREBOARD_DIR
        )
        stdout, stderr = process.communicate(timeout=timeout)
        full_output = stdout + "\n" + stderr
        
        if process.returncode == 0:
            app.logger.info(f"Shell command {' '.join(command_list)} ran successfully.")
            return {'success': True, 'output': full_output}
        else:
            app.logger.warning(f"Shell command {' '.join(command_list)} failed.")
            return {'success': False, 'output': full_output}
            
    except subprocess.TimeoutExpired:
        app.logger.error("Shell command timed out.")
        return {'success': False, 'output': f'Error: Script timed out after {timeout} seconds.'}
    except Exception as e:
        app.logger.error(f"An unexpected error occurred while running shell command: {e}")
        return {'success': False, 'output': f'An unexpected error occurred: {e}'}

def run_plugin_script(args_list, timeout=300):
    """Helper function to run the plugins.py script with given args."""
    if not os.path.exists(PLUGINS_SCRIPT):
        app.logger.error(f"Plugin script not found at {PLUGINS_SCRIPT}")
        return {'success': False, 'output': f'Error: Script not found at {PLUGINS_SCRIPT}'}
        
    command = [PYTHON_EXEC, PLUGINS_SCRIPT] + args_list
    # Call the generic helper, which runs from SCOREBOARD_DIR
    return run_shell_script(command, timeout=timeout)

def parse_plugin_list_output(output):
    """Parses the text table from 'plugins.py list'."""
    plugin_statuses = {}
    lines = output.strip().split('\n')

    if len(lines) <= 2:
        app.logger.warning("Could not parse 'plugins.py list' output: no data lines found.")
        return plugin_statuses

    # Verify header line exists
    header = lines[0]
    if not re.search(r"NAME\s+VERSION\s+STATUS\s+COMMIT", header):
        app.logger.error(f"Could not parse 'plugins.py list' header. Got: {header}")
        return plugin_statuses

    # Parse data lines (skip header at index 0 and separator line at index 1)
    for line in lines[2:]:
        if not line.strip():
            continue

        try:
            # Split by whitespace - this handles variable spacing better than fixed positions
            parts = line.split()

            # We expect at least 4 parts: name, version, status, commit
            if len(parts) >= 4:
                name = parts[0]
                version = parts[1]
                status = parts[2]
                commit = parts[3]

                plugin_statuses[name] = {
                    "version": version,
                    "status": status,
                    "commit": commit
                }
            else:
                app.logger.warning(f"Could not parse plugin list line (expected 4 columns, got {len(parts)}): '{line}'")

        except Exception as e:
            app.logger.warning(f"Could not parse plugin list line: '{line}'. Error: {e}")

    return plugin_statuses

# --- API Endpoints ---

@app.route('/api/status')
def api_status():
    """Provides version and supervisor status to the front-end."""
    
    # Override supervisor check if debug flag is set
    supervisor_status = check_supervisor() or args.debug
    
    return jsonify({
        'version': get_version(),
        'control_hub_version': __version__,
        'supervisor_available': supervisor_status
    })

@app.route('/api/boards')
def api_boards():
    """Returns a list of available boards in the format [{"v": "id", "n": "Name"}]"""
    
    # Hardcoded fallback list - kept for backwards compatibility
    # If a board appears both here AND in scanned builtin boards, an error will be logged
    base_boards_list = [
        'wxalert', 'wxforecast', 'seriesticker',
        'stanley_cup_champions', 'christmas'
    ]
    
    # Get scanned boards
    builtin_boards = get_builtin_boards()
    plugin_boards = get_plugin_boards()
    
    # Check for duplicates between hardcoded and scanned builtin boards
    duplicates = set(base_boards_list) & set(builtin_boards)
    if duplicates:
        app.logger.warning(f"Duplicate boards found in hardcoded list and scanned builtin boards: {sorted(duplicates)}. "
                        f"Remove these from base_boards_list as they are now auto-discovered.")
    
    # Combine all boards and deduplicate (set removes duplicates)
    all_board_ids = list(set(base_boards_list + builtin_boards + plugin_boards))
    all_board_ids.sort()  # Sort alphabetically for consistency
    
    # Convert to format expected by frontend: [{"v": "id", "n": "Name"}]
    boards = [{"v": board_id, "n": board_id.replace('_', ' ').title()} for board_id in all_board_ids]
    
    return jsonify(boards)

@app.route('/load', methods=['GET'])
def load_config():
    """Reads the existing config.json file and returns it."""
    try:
        if not os.path.exists(CONFIG_PATH):
            app.logger.warning(f"Load request failed: {CONFIG_FILE} not found.")
            return jsonify({'success': False, 'message': 'config.json not found.'}), 404
            
        app.logger.info(f"Loading config from: {CONFIG_PATH}")
        with open(CONFIG_PATH, 'r') as f:
            data = json.load(f)
        return jsonify({'success': True, 'config': data})

    except Exception as e:
        app.logger.error(f"Error loading config: {e}")
        return jsonify({'success': False, 'message': f"An error occurred: {e}"}), 500

@app.route('/save', methods=['POST'])
def save_config():
    """Saves the config.json file and creates a backup."""
    try:
        data_string = request.data.decode('utf-8')
        if not os.path.exists(CONFIG_DIR):
            app.logger.info(f"Creating directory: {CONFIG_DIR}")
            os.makedirs(CONFIG_DIR)
        if os.path.exists(CONFIG_PATH):
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            backup_path = f"{CONFIG_PATH}.{timestamp}.bak"
            app.logger.info(f"Backing up existing config to: {backup_path}")
            os.rename(CONFIG_PATH, backup_path)
        app.logger.info(f"Saving new config to: {CONFIG_PATH}")
        with open(CONFIG_PATH, 'w') as f:
            valid_json = json.loads(data_string)
            json.dump(valid_json, f, indent=2)
        return jsonify({'success': True, 'message': f"Config saved to {CONFIG_PATH}. Backup of old file created."})
    except Exception as e:
        app.logger.error(f"Error saving config: {e}")
        return jsonify({'success': False, 'message': f"An error occurred: {e}"}), 500

@app.route('/upload', methods=['POST'])
def upload_config():
    """Reads an uploaded config.json file and returns its content."""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': 'No file part in the request.'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'message': 'No file selected.'}), 400

        if file:
            app.logger.info(f"Processing uploaded file: {file.filename}")
            
            # Read file content
            content = file.read().decode('utf-8')
            
            # Parse JSON to validate
            data = json.loads(content)
            
            # Return the parsed config
            return jsonify({'success': True, 'config': data})

    except json.JSONDecodeError:
        app.logger.error("Upload failed: Invalid JSON in the uploaded file.")
        return jsonify({'success': False, 'message': 'Invalid JSON in file.'}), 400
    except Exception as e:
        app.logger.error(f"Error processing uploaded file: {e}")
        return jsonify({'success': False, 'message': f"An error occurred: {e}"}), 500


@app.route('/api/mqtt-test', methods=['POST'])
def mqtt_test():
    """Runs the mqtt_test.py script."""
    data = request.json
    broker = data.get('broker')
    port = data.get('port')
    username = data.get('username')
    password = data.get('password')

    if not broker or not port:
        return jsonify({'success': False, 'output': 'Error: "broker" and "port" are required.'}), 400

    # The mqtt_test.py script is in the same directory as this server file (SCRIPT_DIR)
    script_path = os.path.join(SCRIPT_DIR, 'mqtt_test.py')
    if not os.path.exists(script_path):
        app.logger.error(f"Script not found at {script_path}")
        return jsonify({'success': False, 'output': f'Error: Script not found at {script_path}'}), 404

    command = [PYTHON_EXEC, script_path, broker, str(port)]
    if username and password:
        command.extend(['-u', username, '-p', password])

    # Use the generic run_shell_script helper which executes from SCOREBOARD_DIR
    result = run_shell_script(command, timeout=30)
    
    # The mqtt_test.py script prints "yes" or "no".
    # We need to check the output for the word "yes" for success.
    if result['success'] and 'yes' in result['output'].lower():
        return jsonify({'success': True, 'output': result['output']})
    else:
        # If the script failed or didn't return "yes", it's a failure.
        return jsonify({'success': False, 'output': result['output']})


@app.route('/api/run-issue-uploader', methods=['POST'])
def run_issue_uploader():
    """Runs the issue_upload.py script and returns its output."""
    app.logger.info("Request received to run issue uploader script...")
    # The script is in the same directory as this server file
    script_path = os.path.join(SCRIPT_DIR, 'issue_upload.py')
    
    if not os.path.exists(script_path):
        app.logger.error(f"Script not found at {script_path}")
        return jsonify({'success': False, 'output': f'Error: Script not found at {script_path}'}), 404
    
    # Call the generic run_shell_script helper to execute the python script
    # PYTHON_EXEC is defined above as sys.executable
    result = run_shell_script([PYTHON_EXEC, script_path, '--scoreboard_dir', SCOREBOARD_DIR], timeout=180)
    return jsonify(result)

# =============================================
# Plugin Management API Endpoints
# =============================================

def download_plugins_index(force=False):
    """
    Downloads the plugins_index.json file.
    If force is False, it will only download if the file doesn't exist.
    If force is True, it will overwrite the existing file.
    """
    PLUGINS_INDEX_URL = "https://raw.githubusercontent.com/falkyre/nhl-led-scoreboard/main/plugins_index.json"
    
    if not force and os.path.exists(PLUGINS_INDEX_FILE):
        app.logger.info(f"{PLUGINS_INDEX_FILE} already exists. Skipping download.")
        return {'success': True, 'message': 'Plugin index already exists.'}

    app.logger.info(f"Downloading plugin index from {PLUGINS_INDEX_URL}...")
    try:
        with urllib.request.urlopen(PLUGINS_INDEX_URL) as response:
            if response.status == 200:
                data = response.read()
                with open(PLUGINS_INDEX_FILE, 'wb') as f:
                    f.write(data)
                app.logger.info(f"Successfully downloaded and saved {PLUGINS_INDEX_FILE}")
                return {'success': True, 'message': 'Plugin index downloaded successfully.'}
            else:
                app.logger.error(f"Failed to download plugin index. Status code: {response.status}")
                return {'success': False, 'message': f"Failed to download. Status: {response.status}"}
    except Exception as e:
        app.logger.error(f"Error downloading plugin index: {e}")
        return {'success': False, 'message': f"An error occurred: {e}"}

@app.route('/api/plugins/refresh', methods=['POST'])
def refresh_plugins_index():
    """API endpoint to force a refresh of the plugins_index.json file."""
    app.logger.info("Request received to refresh plugins index...")
    result = download_plugins_index(force=True)
    return jsonify(result)

@app.route('/api/plugins/status', methods=['GET'])
def get_plugin_status():
    """
    Reads plugins_index.json (for available plugins), plugins.json (for installed plugins),
    and runs 'plugins.py list' (for live status), returning a merged list.
    """
    app.logger.info("Request received for plugin status...")

    # 1. Ensure plugins_index.json exists, downloading if it doesn't.
    download_plugins_index()

    # 2. Get the list of "available" plugins from plugins_index.json
    available_plugins = {}
    try:
        with open(PLUGINS_INDEX_FILE, 'r') as f:
            data = json.load(f)
            if 'plugins' in data and isinstance(data['plugins'], list):
                for plugin in data['plugins']:
                    if 'name' in plugin:
                        available_plugins[plugin['name']] = plugin
    except Exception as e:
        app.logger.error(f"Error reading {PLUGINS_INDEX_FILE}: {e}")
        # We can continue, but the list of available plugins might be empty.

    # 3. Get the list of "installed" plugins from plugins.json
    # This also ensures the file is created if it's missing.
    check_and_create_installed_plugins_file()
    installed_plugins = {}
    try:
        with open(PLUGINS_INSTALLED_FILE, 'r') as f:
            data = json.load(f)
            if 'plugins' in data and isinstance(data['plugins'], list):
                for plugin in data['plugins']:
                    if 'name' in plugin:
                        installed_plugins[plugin['name']] = plugin
    except Exception as e:
        app.logger.error(f"Error reading {PLUGINS_INSTALLED_FILE}: {e}")
        return jsonify({'success': False, 'plugins': [], 'message': str(e)}), 500

    # 4. Get the "live" status from 'plugins.py list'
    list_result = run_plugin_script(['list'], timeout=30)
    if not list_result['success']:
        app.logger.error("Failed to run 'plugins.py list'")
        plugin_statuses = {}
    else:
        plugin_statuses = parse_plugin_list_output(list_result['output'])
    
    app.logger.info(f"Parsed {len(plugin_statuses)} plugin statuses from 'list' command.")

    # 5. Merge all sources
    merged_plugins = {}
    
    # Start with available plugins
    for name, plugin_data in available_plugins.items():
        merged_plugins[name] = {
            "name": name,
            "url": plugin_data.get('url', '-'),
            "version": "-",
            "status": "available",
            "commit": "-"
        }

    # Update with installed info and live status
    all_plugin_names = set(available_plugins.keys()) | set(installed_plugins.keys()) | set(plugin_statuses.keys())

    for name in all_plugin_names:
        if name not in merged_plugins:
             merged_plugins[name] = {
                "name": name,
                "url": installed_plugins.get(name, {}).get('url', '-'),
                "version": "-",
                "status": "unknown",
                "commit": "-"
            }

        status_data = plugin_statuses.get(name)
        if status_data:
            # Plugin is installed according to 'plugins.py list'
            merged_plugins[name]['version'] = status_data.get('version', '-')
            merged_plugins[name]['status'] = status_data.get('status', 'installed')
            merged_plugins[name]['commit'] = status_data.get('commit', '-')
        elif name in installed_plugins:
            # In plugins.json but not in 'list' output -> likely an error or partially removed
             merged_plugins[name]['status'] = 'error'
        # If only in available_plugins, status remains 'available'

    final_plugin_list = sorted(list(merged_plugins.values()), key=lambda p: p['name'])
        
    app.logger.info(f"Returning {len(final_plugin_list)} plugins.")
    return jsonify({'success': True, 'plugins': final_plugin_list})


@app.route('/api/plugins/add', methods=['POST'])
def add_plugin():
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({'success': False, 'output': 'Error: "url" is required.'}), 400
    
    # Command is: python plugins.py add <repo url>
    result = run_plugin_script(['add', url])
    return jsonify(result)

@app.route('/api/plugins/remove', methods=['POST'])
def remove_plugin():
    data = request.json
    name = data.get('name')
    keep_config = data.get('keep_config', False)
    
    if not name:
        return jsonify({'success': False, 'output': 'Error: "name" is required.'}), 400
    
    # Command is: python plugins.py rm <plugin name>
    # Optionally add --keep-config
    command_args = ['rm', name]
    if keep_config:
        command_args.append('--keep-config')
        
    result = run_plugin_script(command_args)
    return jsonify(result)

@app.route('/api/plugins/update', methods=['POST'])
def update_plugin():
    data = request.json
    name = data.get('name')
    if not name:
        return jsonify({'success': False, 'output': 'Error: "name" is required.'}), 400
        
    result = run_plugin_script(['update', name])
    return jsonify(result)

@app.route('/api/plugins/sync', methods=['POST'])
def sync_plugins():
    # Runs 'python plugins.py sync'
    result = run_plugin_script(['sync'])
    return jsonify(result)
    
# =============================================
# End of Plugin API Section
# =============================================

# =============================================
# Logo Editor API Section
# =============================================

def get_logo_editor_path():
    """Returns the absolute path to the logo_editor.py script."""
    return os.path.join(SCOREBOARD_DIR, 'src', 'logo_editor.py')

def check_logo_editor_health(port, host='127.0.0.1', timeout=2):
    """
    Checks if the Logo Editor is running by calling its /api/health endpoint.
    Returns:
        'running': If the endpoint returns 200 OK with status='ok'.
        'available': If the connection is refused (port closed).
        'conflict': If the port is open but returns something else.
    """
    url = f"http://{host}:{port}/api/health"
    app.logger.info(f"Checking Logo Editor health at: {url}")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.status == 200:
                try:
                    content = response.read().decode('utf-8')
                    data = json.loads(content)
                    if data.get('status') == 'ok':
                        app.logger.info(f"Health check PASSED. Response: {content}")
                        return 'running'
                except json.JSONDecodeError:
                    app.logger.warning(f"Health check: Invalid JSON received from {url}")
                    pass
            app.logger.warning(f"Health check: Unexpected response status {response.status} or content from {url}")
            return 'conflict' # Open but not returning expected JSON
            
    except urllib.error.HTTPError as e:
        # If we get an HTTP error (e.g. 404), it means the port IS open and listening, just not our API.
        app.logger.info(f"Health check HTTPError (Port Open): {e.code}")
        return 'conflict'

    except urllib.error.URLError as e:
        # Log the specific error for debugging
        app.logger.info(f"Health check URLError: {e.reason}")
        
        # Check for connection refused
        if isinstance(e.reason, ConnectionRefusedError) or (hasattr(e.reason, 'errno') and e.reason.errno == 111): # 111 is Connection Refused
             return 'available' # Port closed
        
        # Check for timeout
        if isinstance(e.reason, socket.timeout):
             app.logger.warning(f"Health check timed out checking {url}")
             return 'available' # Treat timeout as likely not running properly
        
        # If we get here, it's some other error but usually implies we can't connect properly
        return 'available'

    except Exception as e:
        app.logger.error(f"Health check failed with unexpected error: {e}")
        return 'available'

LOGO_EDITOR_STATE_FILE = os.path.join(SCOREBOARD_DIR, 'logo_editor_state.json')

@app.route('/api/logo-editor/status', methods=['GET'])
def logo_editor_status():
    """Checks if the logo editor script exists and if it's running."""
    path = get_logo_editor_path()
    exists = os.path.exists(path)
    
    # Get port from query parameters, default to 5000
    try:
        port = int(request.args.get('port', 5000))
    except ValueError:
        port = 5000

    # Perform Health Check
    health_status = check_logo_editor_health(port)
    
    # Determine Status
    status = 'unavailable'
    if exists:
        if health_status == 'available':
            status = 'available'
            # Cleanup state file if port is closed, as it means it stopped
            # Cleanup state file if port is closed, BUT only if process is dead
            if os.path.exists(LOGO_EDITOR_STATE_FILE):
                try:
                    with open(LOGO_EDITOR_STATE_FILE, 'r') as f:
                        state = json.load(f)
                        pid = state.get('pid')
                    
                    # Check if process is running
                    is_running = False
                    if pid:
                        try:
                            os.kill(pid, 0) # 0 signal just checks existence
                            is_running = True
                        except OSError:
                            is_running = False
                    
                    if not is_running:
                        app.logger.info(f"Port {port} closed and PID {pid} gone. Cleaning up state file.")
                        os.remove(LOGO_EDITOR_STATE_FILE)
                    else:
                        app.logger.info(f"Port {port} closed but PID {pid} is running (starting up?). Keeping state file.")
                        
                except Exception as e:
                    app.logger.error(f"Error checking/cleaning state file: {e}")
                    # If corrupt, maybe delete? For now, leave it or delete safe
                    pass
        elif health_status == 'running':
            status = 'running'
            # Optional: Check state file to confirm WE launched it?
            # For now, if it's running and compatible, we say it's running.
        else: # conflict
            status = 'conflict' 
            app.logger.info(f"Port {port} is in use by another process (Conflict).")

    # Managed Status
    managed = False
    managed_port = None
    if os.path.exists(LOGO_EDITOR_STATE_FILE):
        managed = True
        try:
            with open(LOGO_EDITOR_STATE_FILE, 'r') as f:
                state = json.load(f)
                managed_port = state.get('port')
        except Exception:
            pass

    app.logger.info(f"Logo Editor Status Check: port={port}, exists={exists}, health_status={health_status}, status={status}")
    
    return jsonify({
        'success': True,
        'available': exists,
        'running': (status == 'running'), # For backwards compatibility if any
        'status': status,
        'port': port,
        'managed': managed,
        'managed_port': managed_port
    })

@app.route('/api/logo-editor/launch', methods=['POST'])
def launch_logo_editor():
    """Launches the logo editor script in the background."""
    app.logger.info("Request received to launch Logo Editor...")
    
    path = get_logo_editor_path()
    app.logger.info(f"Launch: SCOREBOARD_DIR={SCOREBOARD_DIR}")
    app.logger.info(f"Launch: LOGO_EDITOR_STATE_FILE={LOGO_EDITOR_STATE_FILE}")

    if not os.path.exists(path):
        return jsonify({'success': False, 'message': 'logo_editor.py not found.'}), 404

    # Determine Virtual Environment Path and Port
    data = request.json or {}
    venv_path = data.get('venv')
    try:
        port = int(data.get('port', 5000))
    except (ValueError, TypeError):
        port = 5000

    python_to_use = PYTHON_EXEC

    if venv_path:
        # If the user provided a venv path, we want to try to use the python inside it
        # Common locations: venv/bin/python, venv/bin/python3
        possible_pythons = [
            os.path.join(venv_path, 'bin', 'python'),
            os.path.join(venv_path, 'bin', 'python3'),
            os.path.join(venv_path, 'Scripts', 'python.exe'), # Windows just in case
        ]
        
        found_python = False
        for p in possible_pythons:
            if os.path.exists(p):
                python_to_use = p
                found_python = True
                app.logger.info(f"Using venv python: {python_to_use}")
                break
        
        if not found_python:
            app.logger.warning(f"Could not find python in provided venv: {venv_path}. Falling back to default: {PYTHON_EXEC}")

    elif not venv_path: 
        # Only guess/default venv path if NOT provided (and thus we are using system python as base for now, unless we change that too)
        # However, the user request says "if the user provides a virtual environment path, the editor should be launched with the python in that venv"
        # Logic:
        # 1. If venv provided -> Clean it, check for python, use that python.
        # 2. If venv NOT provided -> Default logic (maybe try to guess valid venv for --venv arg, but keep running with default python? 
        #    Actually, the current code WAS running 'python3 src/logo_editor.py --venv ...' using PYTHON_EXEC.
        #    So we should stick to using PYTHON_EXEC unless venv is explicit.
        
        # Try to guess the user to construct the default path for the argument to pass to the script
        user = os.environ.get('SUDO_USER') or os.environ.get('USER') or 'pi'
        venv_path = f"/home/{user}/nhlsb-venv/"
        app.logger.info(f"No venv provided. Defaulting to: {venv_path}")

    # Construct the command
    # If we found a specific python in the venv, we use that as the executable.
    # We still pass --venv to the script because the script likely uses it for other things (like hot-reloading or internal logic).
    command = [
        python_to_use, 
        path, 
        '--venv', venv_path, 
        '--dir', SCOREBOARD_DIR,
        '--port', str(port)
    ]
    
    app.logger.info(f"Launching command: {' '.join(command)}")

    # Check for Flask in the target environment
    try:
        check_cmd = [python_to_use, '-c', 'import flask']
        app.logger.info(f"Checking for flask in {python_to_use}...")
        check_result = subprocess.run(check_cmd, capture_output=True, text=True)
        
        if check_result.returncode != 0:
            error_msg = f"Flask is not installed in the selected environment. Check output: {check_result.stderr or check_result.stdout}"
            app.logger.error(error_msg)
            return jsonify({'success': False, 'message': f'Flask is not installed in the selected environment ({venv_path or "default"}). Please install it or choose a valid venv.'}), 400
            
    except Exception as e:
        app.logger.error(f"Failed to check for flask: {e}")
        # Proceed with caution or fail? Failsafe to fail is probably better to avoid silent failure of the main process
        return jsonify({'success': False, 'message': f'Failed to validate environment: {e}'}), 500

    # Prepare environment for the subprocess
    env = os.environ.copy()
    # Remove Flask reloader variables to prevent KeyError/Conflict in subprocess
    env.pop('WERKZEUG_SERVER_FD', None)
    env.pop('WERKZEUG_RUN_MAIN', None)
    
    if venv_path:
        # Explicitly set VIRTUAL_ENV and update PATH
        env['VIRTUAL_ENV'] = venv_path
        # Prepend venv bin to PATH
        env['PATH'] = os.path.join(venv_path, 'bin') + os.pathsep + env.get('PATH', '')
        # Unset PYTHONHOME if it exists to avoid conflicts
        env.pop('PYTHONHOME', None)

    try:
        # Launch as detached process
        
        stdout_dest = subprocess.DEVNULL
        stderr_dest = subprocess.DEVNULL
        debug_log_file = None

        if args.debug:
            try:
                log_path = os.path.join(SCOREBOARD_DIR, 'logo_editor_debug.log')
                app.logger.info(f"Debug mode enabled: Redirecting Logo Editor output to {log_path}")
                debug_log_file = open(log_path, 'w')
                stdout_dest = debug_log_file
                stderr_dest = subprocess.STDOUT
            except Exception as e:
                app.logger.error(f"Failed to open debug log file: {e}")

        process = subprocess.Popen(
            command, 
            cwd=SCOREBOARD_DIR,
            stdout=stdout_dest,
            stderr=stderr_dest,
            start_new_session=True,
            env=env
        )

        if debug_log_file:
            debug_log_file.close()
        
        # Save state
        try:
            with open(LOGO_EDITOR_STATE_FILE, 'w') as f:
                json.dump({'port': port, 'pid': process.pid}, f)
        except Exception as e:
            app.logger.error(f"Failed to write logo editor state file: {e}")

        return jsonify({'success': True, 'message': 'Logo Editor launch command issued.', 'port': port})
    except Exception as e:
        app.logger.error(f"Failed to launch Logo Editor: {e}")
        return jsonify({'success': False, 'message': f"Failed to launch: {e}"}), 500

def shutdown_logo_editor():
    """
    Stops the running Logo Editor process if it exists.
    Returns a dict with 'success' (bool) and 'message' (str).
    """
    if not os.path.exists(LOGO_EDITOR_STATE_FILE):
        return {'success': False, 'message': 'No running Logo Editor tracked.'}

    try:
        with open(LOGO_EDITOR_STATE_FILE, 'r') as f:
            state = json.load(f)
            pid = state.get('pid')
            
        if pid:
            try:
                # Terminate the process
                os.kill(pid, 15) # SIGTERM
                # Optionally wait loop could go here, but for now we just send the signal
                app.logger.info(f"Sent SIGTERM to process {pid}")
            except ProcessLookupError:
                app.logger.warning(f"Process {pid} not found. Cleaning up state file.")
            except Exception as e:
                app.logger.error(f"Failed to kill process {pid}: {e}")
                return {'success': False, 'message': f"Failed to stop process: {e}"}
        
        # Clean up state file on success or if process was missing
        if os.path.exists(LOGO_EDITOR_STATE_FILE):
             os.remove(LOGO_EDITOR_STATE_FILE)

        return {'success': True, 'message': 'Logo Editor stopped.'}

    except Exception as e:
        app.logger.error(f"Error stopping Logo Editor: {e}")
        return {'success': False, 'message': f"An error occurred: {e}"}

@app.route('/api/logo-editor/stop', methods=['POST'])
def stop_logo_editor():
    """Stops the running Logo Editor process."""
    app.logger.info("Request received to stop Logo Editor...")
    
    result = shutdown_logo_editor()
    
    if result['success']:
        return jsonify(result)
    else:
        # If message says "No running...", return 404, else 500
        if 'No running' in result['message']:
            return jsonify(result), 404
        else:
            return jsonify(result), 500

# Register cleanup on exit
atexit.register(shutdown_logo_editor)

# =============================================
# End of Logo Editor API Section
# =============================================

# =============================================
# Supervisor XML-RPC API Endpoints
# =============================================
@app.route('/api/supervisor/processes', methods=['GET'])
def api_supervisor_processes():
    """Fetches all process info from Supervisor."""
    try:
        with xmlrpc.client.ServerProxy(f'http://{SUPERVISOR_URL}:{SUPERVISOR_PORT}/RPC2') as proxy:
            processes = proxy.supervisor.getAllProcessInfo()
            return jsonify({'success': True, 'processes': processes})
    except Exception as e:
        app.logger.error(f"XML-RPC Error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/supervisor/start', methods=['POST'])
def api_supervisor_start():
    """Starts a process via Supervisor."""
    name = request.json.get('name')
    try:
        with xmlrpc.client.ServerProxy(f'http://{SUPERVISOR_URL}:{SUPERVISOR_PORT}/RPC2') as proxy:
            result = proxy.supervisor.startProcess(name)
            return jsonify({'success': True, 'result': result})
    except Exception as e:
        app.logger.error(f"XML-RPC Start Error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/supervisor/stop', methods=['POST'])
def api_supervisor_stop():
    """Stops a process via Supervisor."""
    name = request.json.get('name')
    try:
        with xmlrpc.client.ServerProxy(f'http://{SUPERVISOR_URL}:{SUPERVISOR_PORT}/RPC2') as proxy:
            result = proxy.supervisor.stopProcess(name)
            return jsonify({'success': True, 'result': result})
    except Exception as e:
        app.logger.error(f"XML-RPC Stop Error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/supervisor/tail_stderr', methods=['POST'])
def api_supervisor_tail_stderr():
    """Tails the stderr log of a process."""
    name = request.json.get('name')
    # Read the last 4KB (4096 bytes) of the log
    offset = -4096 
    length = 4096
    try:
        with xmlrpc.client.ServerProxy(f'http://{SUPERVISOR_URL}:{SUPERVISOR_PORT}/RPC2') as proxy:
            # Returns [log_data, offset, overflow]
            result = proxy.supervisor.tailProcessStderrLog(name, offset, length)
            return jsonify({'success': True, 'log': result[0], 'offset': result[1], 'overflow': result[2]})
    except Exception as e:
        app.logger.error(f"XML-RPC Log Error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500
# =============================================

# --- Page Serving ---

@app.route('/')
def index():
    """
    Serves the main index.html page or redirects to setup.html
    if the SETUP file exists.
    """
    # Bypass setup check if in debug mode
    if check_first_run() and not args.debug:
        app.logger.info(f"SETUP file found. Serving setup.html for {request.remote_addr}")
        return send_from_directory(TEMPLATES_DIR, 'setup.html') 
        
    return send_from_directory(TEMPLATES_DIR, 'index.html')

@app.route('/setup')
def setup_page():
    """Serves the setup.html page."""
    # Bypass setup check if in debug mode
    if not check_first_run() and not args.debug:
        app.logger.info("Access to /setup denied, redirecting to /")
        return send_from_directory(TEMPLATES_DIR, 'index.html') 
    
    app.logger.info(f"Serving setup.html (Debug: {args.debug})")
    return send_from_directory(TEMPLATES_DIR, 'setup.html') 


@app.route('/config')
def config_page():
    """Serves the configurator page."""
    return send_from_directory(TEMPLATES_DIR, 'config.html') 

@app.route('/utilities')
def utilities_page():
    """Serves the placeholder utilities page."""
    return send_from_directory(TEMPLATES_DIR, 'utilities.html') 

@app.route('/download_config')
def download_config():
    """
    Provides a download of configuration files.
    If on a full scoreboard setup or if logos are requested, it zips up multiple configs.
    Otherwise, it just provides the config.json.
    """
    led_portal_dir = '/home/pi/.nhlledportal'
    include_logos = request.args.get('logos') == 'true'
    
    try:
        if os.path.exists(led_portal_dir) or include_logos:
            app.logger.info(f"Zipping configuration files (Full Setup: {os.path.exists(led_portal_dir)}, Include Logos: {include_logos}).")
            
            # Create an in-memory zip file
            memory_file = io.BytesIO()
            with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                
                # 1. Add config.json
                if os.path.exists(CONFIG_PATH):
                    zf.write(CONFIG_PATH, os.path.basename(CONFIG_PATH))
                    app.logger.info(f"Added {CONFIG_PATH} to zip.")
                else:
                    app.logger.warning(f"{CONFIG_PATH} not found, skipping.")

                # 2. Add supervisor config
                supervisor_conf_path = '/etc/supervisor/conf.d/scoreboard.conf'
                if os.path.exists(supervisor_conf_path):
                    zf.write(supervisor_conf_path, os.path.basename(supervisor_conf_path))
                    app.logger.info(f"Added {supervisor_conf_path} to zip.")
                else:
                    if os.path.exists(led_portal_dir): # Only warn if we expect it
                        app.logger.warning(f"{supervisor_conf_path} not found, skipping.")

                # 3. Add testMatrix.sh script
                matrix_sh_path = '/home/pi/sbtools/testMatrix.sh'
                if os.path.exists(matrix_sh_path):
                    zf.write(matrix_sh_path, os.path.basename(matrix_sh_path))
                    app.logger.info(f"Added {matrix_sh_path} to zip.")
                else:
                    if os.path.exists(led_portal_dir):
                        app.logger.warning(f"{matrix_sh_path} not found, skipping.")

                # 4. Add splash.sh script
                splash_sh_path = '/home/pi/sbtools/splash.sh'
                if os.path.exists(splash_sh_path):
                    zf.write(splash_sh_path, os.path.basename(splash_sh_path))
                    app.logger.info(f"Added {splash_sh_path} to zip.")
                else:
                    if os.path.exists(led_portal_dir):
                        app.logger.warning(f"{splash_sh_path} not found, skipping.")

                # 5. Add Logos if requested
                if include_logos:
                    # Add layout files (config/layout/logos_{W}x{H}.json)
                    layout_dir = os.path.join(SCOREBOARD_DIR, 'config', 'layout')
                    if os.path.exists(layout_dir):
                        # Use glob to find matching files
                        layout_files = glob.glob(os.path.join(layout_dir, 'logos_*x*.json'))
                        for layout_file in layout_files:
                            # Add to 'layout/' directory in zip
                            zf.write(layout_file, os.path.join('layout', os.path.basename(layout_file)))
                            app.logger.info(f"Added {layout_file} to zip.")
                    
                    # Add logos assets (assets/logos)
                    logos_dir = os.path.join(SCOREBOARD_DIR, 'assets', 'logos')
                    if os.path.exists(logos_dir):
                        app.logger.info(f"Adding logos from {logos_dir}...")
                        for root, dirs, files in os.walk(logos_dir):
                            for file in files:
                                full_path = os.path.join(root, file)
                                # Calculate path inside zip (relative to logos_dir)
                                # We want them in a 'logos' folder in the zip
                                rel_path = os.path.relpath(full_path, start=logos_dir)
                                zip_path = os.path.join('logos', rel_path)
                                zf.write(full_path, zip_path)

            memory_file.seek(0)
            return send_file(
                memory_file,
                mimetype='application/zip',
                as_attachment=True,
                download_name='configs.zip'
            )
        else:
            app.logger.info(f"'{led_portal_dir}' not found. Sending single config.json file.")
            return send_file(
                CONFIG_PATH,
                mimetype='application/json',
                as_attachment=True,
                download_name='config.json'
            )
    except FileNotFoundError:
        app.logger.error(f"Could not find {CONFIG_PATH} for download.")
        return "config.json not found.", 404
    except Exception as e:
        app.logger.error(f"An error occurred during config download: {e}")
        return "An internal error occurred.", 500


@app.route('/plugins')
def plugins_page():
    """Serves the new plugins page."""
    return send_from_directory(TEMPLATES_DIR, 'plugins.html')

@app.route('/supervisor')
def supervisor_page():
    """Serves the supervisor embed page."""
    return send_from_directory(TEMPLATES_DIR, 'supervisor_rpc.html') 

@app.route('/logo_editor')
def logo_editor_page():
    """Serves the logo editor embed page."""
    return send_from_directory(TEMPLATES_DIR, 'logo_editor_embed.html') 

@app.route('/assets/<path:path>')
def send_asset(path):
    """Serves files from the assets directory (like the logo)."""
    return send_from_directory(ASSETS_DIR, path) 


# =============================================
# Web Terminal Logic
# =============================================

# Store active sessions: 
# { 
#   token: {
#       'client': paramiko.SSHClient, 
#       'shell': channel, 
#       'sid': str (current connected sid, or None),
#       'cleanup_timer': gevent.Greenlet (or None)
#   } 
# }
ssh_sessions = {}
SESSION_TIMEOUT = 600  # 10 minutes

def cleanup_session(token):
    """
    Background task to clean up a session after timeout.
    """
    app.logger.info(f"Cleanup task started for token {token}...")
    # Wait for the timeout
    gevent.sleep(SESSION_TIMEOUT)
    
    # Check if still disconnected
    if token in ssh_sessions:
        session = ssh_sessions[token]
        if session.get('sid') is None:
            app.logger.info(f"Session {token} timed out. Closing connection.")
            try:
                session['shell'].close()
                session['client'].close()
            except Exception:
                pass
            del ssh_sessions[token]
        else:
            app.logger.info(f"Session {token} reconnected. Cleanup aborted.")

def read_from_ssh(token, shell):
    """
    Background thread/task to read from the SSH shell
    and emit 'response' events to the specific room (token).
    """
    while True:
        try:
            # Check if there is data to read
            if shell.recv_ready():
                data = shell.recv(1024).decode('utf-8')
                # Emit to the room named by the token
                socketio.emit('response', {'data': data}, room=token)
            
            # Check if the process has exited
            if shell.exit_status_ready():
                break
            
            # Use socketio.sleep for async compatibility
            socketio.sleep(0.01)
        except Exception as e:
            app.logger.error(f"Error reading from SSH for token {token}: {e}")
            break

@app.route('/terminal')
def terminal_page():
    """Serves the web terminal page."""
    return send_from_directory(TEMPLATES_DIR, 'ssh_index.html')

@socketio.on('ssh_login')
def handle_ssh_login(data):
    sid = request.sid
    # HARDCODED ACROSS-THE-BOARD
    hostname = '127.0.0.1'
    port = 22
    
    username = data.get('username')
    password = data.get('password')

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Connect to localhost
        client.connect(hostname, port=port, username=username, password=password)
        
        # 'xterm-256color' is often better for zsh/powerlevel10k than standard 'xterm'
        shell = client.invoke_shell(term='xterm-256color')
        
        # Generate a unique token for this session
        token = str(uuid.uuid4())
        
        ssh_sessions[token] = {
            'client': client,
            'shell': shell,
            'sid': sid,
            'cleanup_timer': None
        }
        
        # Join the room specific to this session
        join_room(token)
        
        # Start the background task to read output
        socketio.start_background_task(target=read_from_ssh, token=token, shell=shell)
        
        emit('login_status', {'status': 'success', 'token': token})
        app.logger.info(f"SSH login successful for user '{username}'. Token: {token}")
        
    except Exception as e:
        app.logger.error(f"SSH login failed for user '{username}': {e}")
        emit('login_status', {'status': 'error', 'message': str(e)})

@socketio.on('ssh_resume')
def handle_ssh_resume(data):
    sid = request.sid
    token = data.get('token')
    
    if token in ssh_sessions:
        session = ssh_sessions[token]
        
        # Cancel any cleanup timer if it exists
        if session.get('cleanup_timer'):
            try:
                session['cleanup_timer'].kill()
            except Exception:
                pass
            session['cleanup_timer'] = None
            
        # Update SID and join room
        session['sid'] = sid
        join_room(token)
        
        emit('login_status', {'status': 'success', 'token': token})
        app.logger.info(f"Session resumed for token {token}")
        
        # Determine if shell is active?
        if not session['shell'].active:
             emit('response', {'data': '\r\nSession closed by server.\r\n'})
    else:
        emit('login_status', {'status': 'error', 'message': 'Session expired or invalid'})

@socketio.on('input')
def handle_input(message):
    token = message.get('token') # Client must send token with input
    if token in ssh_sessions:
        session = ssh_sessions[token]
        try:
            session['shell'].send(message['data'])
        except Exception as e:
            app.logger.error(f"Error sending input to SSH for token {token}: {e}")

@socketio.on('disconnect')
def disconnect_user():
    sid = request.sid
    # Find which token belongs to this SID
    target_token = None
    for token, session in ssh_sessions.items():
        if session['sid'] == sid:
            target_token = token
            break
            
    if target_token:
        # Mark as disconnected but don't close yet
        ssh_sessions[target_token]['sid'] = None
        leave_room(target_token)
        
        # Start cleanup timer
        ssh_sessions[target_token]['cleanup_timer'] = gevent.spawn(cleanup_session, target_token)
        
        app.logger.info(f"Client disconnected. Session {target_token} will be kept alive for {SESSION_TIMEOUT}s.")

@socketio.on('ssh_logout')
def handle_logout(data):
    token = data.get('token')
    if token in ssh_sessions:
        session = ssh_sessions[token]
        try:
            session['shell'].close()
            session['client'].close()
        except Exception:
            pass
        del ssh_sessions[token]
        app.logger.info(f"User logged out. Session {token} terminated.")

# --- Run the Server ---
if __name__ == '__main__':
    if args.debug:
        app.logger.warning("="*50)
        app.logger.warning("Flask running in DEBUG mode.")
        app.logger.warning("Setup and Supervisor checks will be bypassed.")
        app.logger.warning("="*50)
        
    app.logger.info(f"Starting NHL Scoreboard Config Server on port {PORT}")
    app.logger.info(f"Serving HTML files from: {TEMPLATES_DIR}")
    app.logger.info(f"Serving Assets from: {ASSETS_DIR}")
    app.logger.info(f"Access at http://[YOUR_PI_IP]:{PORT} in your browser.")
    
    # Use socketio.run instead of app.run
    socketio.run(app, host='0.0.0.0', port=PORT, debug=args.debug)