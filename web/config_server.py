import os
import json
import socket
import logging  
import argparse
import subprocess 
import sys
import shutil
import re
import xmlrpc.client # <-- NEW
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from richcolorlog import RichColorLogHandler  

# --- Command-Line Argument Parsing ---
parser = argparse.ArgumentParser(description='Flask server for the NHL LED Scoreboard Control Hub.')
parser.add_argument(
    '-d', '--scoreboard_dir', 
    default='.', 
    help='Path to the root of the nhl-led-scoreboard directory (where VERSION and plugins.json are located). Defaults to the current directory.'
)
parser.add_argument(
    '--debug',
    action='store_true',
    help='Run Flask in debug mode and show all pages for testing.'
)
args = parser.parse_args()
SCOREBOARD_DIR = os.path.abspath(args.scoreboard_dir)
# Get the directory the script itself is in
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# --- End Argument Parsing ---

# --- Configuration ---
PORT = 8000
# Paths relative to --scoreboard_dir
CONFIG_DIR = os.path.join(SCOREBOARD_DIR, 'config')
CONFIG_FILE = 'config.json'
CONFIG_PATH = os.path.join(CONFIG_DIR, CONFIG_FILE)
VERSION_FILE = os.path.join(SCOREBOARD_DIR, 'VERSION')
PLUGINS_FILE = os.path.join(SCOREBOARD_DIR, 'plugins.json')
PLUGINS_EXAMPLE_FILE = os.path.join(SCOREBOARD_DIR, 'plugins.json.example')
PLUGINS_LOCK_FILE = os.path.join(SCOREBOARD_DIR, 'plugins.lock.json')
PLUGINS_SCRIPT = os.path.join(SCOREBOARD_DIR, 'plugins.py')
PYTHON_EXEC = sys.executable

# ASSETS_DIR is relative to the script's location
ASSETS_DIR = os.path.join(SCRIPT_DIR, 'assets') 

# Absolute paths
SETUP_FILE = '/home/pi/.nhlledportal/SETUP'
SUPERVISOR_URL = '127.0.0.1'
SUPERVISOR_PORT = 9001
# --- End Configuration ---

# =============================================
# Logging Setup
# =============================================
# Set log level based on debug flag
log_level = logging.DEBUG if args.debug else logging.INFO

# 1. Set up the RichColorLogHandler
handler = RichColorLogHandler(
    level=log_level,
    show_time=True,
    show_level=True,
    markup=True,
    show_background=False
)

# 2. Get the Flask app's logger
app = Flask(__name__)
app.logger.handlers = []  # Remove the default handler
app.logger.addHandler(handler)
app.logger.setLevel(log_level)
app.logger.propagate = False  # Don't propagate to the root logger

# 3. Get the Werkzeug logger (handles request logs)
werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.handlers = []  # Remove its default handlers
werkzeug_logger.addHandler(handler)
werkzeug_logger.setLevel(log_level)
# =============================================


# --- Helper Functions ---

def check_first_run():
    """Checks if the first-run SETUP file exists."""
    return os.path.exists(SETUP_FILE)

def check_and_create_plugins_file():
    """Checks for plugins.json, creates it from .example if missing."""
    if not os.path.exists(PLUGINS_FILE):
        app.logger.warning(f"{PLUGINS_FILE} not found. Checking for example file...")
        if os.path.exists(PLUGINS_EXAMPLE_FILE):
            try:
                shutil.copy(PLUGINS_EXAMPLE_FILE, PLUGINS_FILE)
                app.logger.info(f"Successfully created {PLUGINS_FILE} from {PLUGINS_EXAMPLE_FILE}")
            except Exception as e:
                app.logger.error(f"Failed to copy {PLUGINS_EXAMPLE_FILE} to {PLUGINS_FILE}: {e}")
        else:
            app.logger.warning(f"{PLUGINS_EXAMPLE_FILE} not found. A plugins.json file could not be created.")

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
    check_and_create_plugins_file()
    
    board_names = []
    try:
        with open(PLUGINS_FILE, 'r') as f:
            data = json.load(f)
            # Check if 'plugins' key exists and is a list
            if 'plugins' in data and isinstance(data['plugins'], list):
                for plugin in data['plugins']:
                    # Get the name from each plugin object
                    if 'name' in plugin:
                        board_names.append(plugin['name'])
                app.logger.info(f"Loaded {len(board_names)} plugin boards: {board_names}")
            else:
                app.logger.warning(f"{PLUGINS_FILE} is missing 'plugins' key or it's not a list.")
    except FileNotFoundError:
        app.logger.info(f"{PLUGINS_FILE} not found, no custom boards loaded.")
    except json.JSONDecodeError:
        app.logger.error(f"Could not decode {PLUGINS_FILE}. Check for JSON syntax errors.")
    except Exception as e:
        app.logger.error(f"Error reading {PLUGINS_FILE}: {e}")
    
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

    # Find the header line to get column positions
    header = lines[0]
    # Use regex to find column names, allowing for variable whitespace
    header_matches = re.search(r"^(NAME)\s+(VERSION)\s+(STATUS)\s+(COMMIT)\s*$", header)
    if not header_matches:
        app.logger.error(f"Could not parse 'plugins.py list' header. Got: {header}")
        return plugin_statuses

    # Get the start index of each column
    name_pos = header_matches.start(1)
    version_pos = header_matches.start(2)
    status_pos = header_matches.start(3)
    commit_pos = header_matches.start(4)

    # Parse data lines
    for line in lines[2:]: # Skip header and '---' line
        if not line.strip():
            continue
            
        try:
            # Extract data based on column start positions
            name = line[name_pos:version_pos].strip()
            version = line[version_pos:status_pos].strip()
            status = line[status_pos:commit_pos].strip()
            commit = line[commit_pos:].strip()
            
            if name:
                plugin_statuses[name] = {
                    "version": version,
                    "status": status,
                    "commit": commit
                }
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

@app.route('/api/run-issue-uploader', methods=['POST'])
def run_issue_uploader():
    """Runs the issueUpload.sh script and returns its output."""
    app.logger.info("Request received to run issue uploader script...")
    script_path = os.path.join(SCOREBOARD_DIR, 'scripts', 'sbtools', 'issueUpload.sh')
    if not os.path.exists(script_path):
        app.logger.error(f"Script not found at {script_path}")
        return jsonify({'success': False, 'output': f'Error: Script not found at {script_path}'}), 404
    
    # Call the generic run_shell_script helper
    result = run_shell_script([script_path], timeout=120)
    return jsonify(result)

# =============================================
# NEW: Supervisor XML-RPC API Endpoints
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


# =============================================
# Plugin Management API Endpoints
# =============================================

@app.route('/api/plugins/status', methods=['GET'])
def get_plugin_status():
    """
    Reads plugins.json (for URL/ref) and runs 'plugins.py list' (for status)
    and returns a merged list of all plugins.
    """
    app.logger.info("Request received for plugin status...")
    
    # 1. Get the "available" plugins from plugins.json
    check_and_create_plugins_file()
    available_plugins = []
    try:
        with open(PLUGINS_FILE, 'r') as f:
            data = json.load(f)
            if 'plugins' in data and isinstance(data['plugins'], list):
                available_plugins = data['plugins']
    except Exception as e:
        app.logger.error(f"Error reading {PLUGINS_FILE}: {e}")
        return jsonify({'success': False, 'plugins': [], 'message': str(e)}), 500

    # 2. Get the "actual" status from 'plugins.py list'
    list_result = run_plugin_script(['list'], timeout=30)
    if not list_result['success']:
        app.logger.error("Failed to run 'plugins.py list'")
        # Don't fail the whole request; just return info from plugins.json
        plugin_statuses = {}
    else:
        plugin_statuses = parse_plugin_list_output(list_result['output'])
    
    app.logger.info(f"Parsed {len(plugin_statuses)} plugin statuses from 'list' command.")

    # 3. Merge the two lists
    merged_plugins = []
    # We iterate over plugins.json as the "source of truth" for what *should* be available
    for plugin in available_plugins:
        name = plugin.get('name')
        if not name:
            continue
            
        # Check if this plugin is in the 'list' output
        status_data = plugin_statuses.get(name)
        
        if status_data:
            # It was found in the 'list' output
            version = status_data.get('version', '-')
            status = status_data.get('status', 'unknown')
            commit = status_data.get('commit', '-')
        else:
            # It's in plugins.json but not in 'list' output (e.g., error, or 'list' only shows installed)
            version = '-'
            commit = '-'
            status = 'missing' # Assume 'missing' if not in the 'list' output
            
        merged_plugins.append({
            "name": name,
            "url": plugin.get('url', '-'),
            "version": version,
            "status": status,
            "commit": commit
        })
        
    app.logger.info(f"Returning {len(merged_plugins)} plugins.")
    return jsonify({'success': True, 'plugins': merged_plugins})


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
        return send_from_directory(SCRIPT_DIR, 'setup.html') 
        
    return send_from_directory(SCRIPT_DIR, 'index.html')

@app.route('/setup')
def setup_page():
    """Serves the setup.html page."""
    # Bypass setup check if in debug mode
    if not check_first_run() and not args.debug:
        app.logger.info("Access to /setup denied, redirecting to /")
        return send_from_directory(SCRIPT_DIR, 'index.html') 
    
    app.logger.info(f"Serving setup.html (Debug: {args.debug})")
    return send_from_directory(SCRIPT_DIR, 'setup.html') 


@app.route('/config')
def config_page():
    """Serves the configurator page."""
    return send_from_directory(SCRIPT_DIR, 'config.html') 

@app.route('/utilities')
def utilities_page():
    """Serves the placeholder utilities page."""
    return send_from_directory(SCRIPT_DIR, 'utilities.html') 

@app.route('/plugins')
def plugins_page():
    """Serves the new plugins page."""
    return send_from_directory(SCRIPT_DIR, 'plugins.html')

@app.route('/supervisor')
def supervisor_page():
    """Serves the supervisor embed page."""
    return send_from_directory(SCRIPT_DIR, 'supervisor_rpc.html') 

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
    app.logger.info(f"Serving HTML files from: {SCRIPT_DIR}")
    app.logger.info(f"Serving Assets from: {ASSETS_DIR}")
    app.logger.info(f"Access at http://[YOUR_PI_IP]:{PORT} in your browser.")
    
    # Use the debug flag from args
    app.run(host='0.0.0.0', port=PORT, debug=args.debug)