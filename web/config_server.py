import os
import json
import socket
import logging  
import argparse
import subprocess 
import sys
import shutil
import re
import xmlrpc.client
import urllib.request
import toml
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from richcolorlog import RichColorLogHandler

__version__ = "2025.12.0"

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

def get_plugin_boards():
    """Reads plugins.json and returns a list of board names."""
    
    # Run check to create plugins.json if it's missing
    check_and_create_installed_plugins_file()
    
    board_names = []
    try:
        with open(PLUGINS_INSTALLED_FILE, 'r') as f:
            data = json.load(f)
            # Check if 'plugins' key exists and is a list
            if 'plugins' in data and isinstance(data['plugins'], list):
                for plugin in data['plugins']:
                    # Get the name from each plugin object
                    if 'name' in plugin:
                        board_names.append(plugin['name'])
                app.logger.info(f"Loaded {len(board_names)} plugin boards: {board_names}")
            else:
                app.logger.warning(f"{PLUGINS_INSTALLED_FILE} is missing 'plugins' key or it's not a list.")
    except FileNotFoundError:
        app.logger.info(f"{PLUGINS_INSTALLED_FILE} not found, no custom boards loaded.")
    except json.JSONDecodeError:
        app.logger.error(f"Could not decode {PLUGINS_INSTALLED_FILE}. Check for JSON syntax errors.")
    except Exception as e:
        app.logger.error(f"Error reading {PLUGINS_INSTALLED_FILE}: {e}")
    
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
    """Provides a list of all available boards (built-in + plugins)."""
    
    # Base list (as requested, "holiday_countdown" is removed)
    base_boards_list = [
        "wxalert", "wxforecast", "scoreticker", "seriesticker", "standings",
        "team_summary", "stanley_cup_champions", "christmas",
        "season_countdown", "clock", "weather", "player_stats", "ovi_tracker", "stats_leaders"
    ]
    
    # Get custom boards from plugins.json
    plugin_boards = get_plugin_boards()
    
    # Combine and return the lists
    all_boards = base_boards_list + plugin_boards
    
    # Create the object format the front-end expects
    board_options = [{"v": name, "n": name.replace("_", " ").title()} for name in all_boards]
    
    return jsonify(board_options)

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

@app.route('/plugins')
def plugins_page():
    """Serves the new plugins page."""
    return send_from_directory(TEMPLATES_DIR, 'plugins.html')

@app.route('/supervisor')
def supervisor_page():
    """Serves the supervisor embed page."""
    return send_from_directory(TEMPLATES_DIR, 'supervisor_rpc.html') 

@app.route('/assets/<path:path>')
def send_asset(path):
    """Serves files from the assets directory (like the logo)."""
    return send_from_directory(ASSETS_DIR, path) 


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
    
    # Use the debug flag from args
    app.run(host='0.0.0.0', port=PORT, debug=args.debug)