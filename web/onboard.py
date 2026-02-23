import os
import json
import subprocess
import logging

log = logging.getLogger(__name__)

STATUS_FILE = '/home/pi/.nhlledportal/status'
SETUP_FILE = '/home/pi/.nhlledportal/SETUP'
TEST_SCRIPT_PATH = '/home/pi/sbtools/testMatrix.sh'
SUPERVISOR_CONF = '/etc/supervisor/conf.d/scoreboard.conf'

def get_pi_model_slowdown():
    """Reads the device tree model to determine the appropriate slowdown."""
    try:
        with open('/sys/firmware/devicetree/base/model', 'r') as f:
            model = f.read().strip()
            log.info(f"Detected Raspberry Pi model: {model}")
            if "Raspberry Pi 4" in model:
                return "--led-slowdown-gpio=4"
            else:
                return "--led-slowdown-gpio=2"
    except Exception as e:
        log.warning(f"Could not read Pi model, defaulting to slowdown 2: {e}")
        return "--led-slowdown-gpio=2"

def read_status_file():
    """Reads the status file or returns the fallback text."""
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, 'r') as f:
                status = f.read().strip()
                return status if status else "You are running a TEST version"
        else:
            return "You are running a TEST version"
    except Exception:
        return "You are running a TEST version"

def create_config(team_name, scoreboard_dir, debug=False):
    """
    Reads config.json.sample, sets the team, removes 'standings' from states, 
    and saves as config.json.
    """
    config_dir = os.path.join(scoreboard_dir, 'config')
    sample_path = os.path.join(config_dir, 'config.json.sample')
    dest_path = os.path.join(config_dir, 'config.json')

    if not os.path.exists(sample_path):
        return False, f"Sample config not found at {sample_path}"

    try:
        with open(sample_path, 'r') as f:
            config_data = json.load(f)

        # 1. Update preferences.teams
        if 'preferences' not in config_data:
            config_data['preferences'] = {}
        config_data['preferences']['teams'] = [team_name]

        # 2. Iterate states and remove "standings"
        if 'states' in config_data:
            for state_key, state_list in config_data['states'].items():
                if isinstance(state_list, list) and "standings" in state_list:
                    state_list.remove("standings")

        # Formatted JSON string
        json_str = json.dumps(config_data, indent=4)

        if debug:
            log.info("Debug mode: Returning generated config JSON instead of saving.")
            return True, json_str

        # Save to destination
        with open(dest_path, 'w') as f:
            f.write(json_str)
            
        return True, "Configuration created successfully."
    except Exception as e:
        log.error(f"Failed to create config.json: {e}")
        return False, str(e)


def generate_test_script(board_command, debug=False):
    """Generates the testMatrix.sh script using frontend values."""
    status_text = read_status_file()
    
    script_content = f"""#!/bin/bash
echo 'Watch your display for $(tput setaf 3){status_text}$(tput sgr0) to be displayed'
echo 'This will run for about 15 seconds and then exit itself'
cd /home/pi/nhl-led-scoreboard/submodules/matrix/bindings/python/samples
sudo /home/pi/nhlsb-venv/bin/python3 runtext.py {board_command} -y 20 -l 1 -C 255,255,0 -t '{status_text}' >/dev/null 2>&1
clear
exit
"""

    if debug:
        log.info("Debug mode enabled: Returning script content instead of creating file.")
        return True, script_content

    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(TEST_SCRIPT_PATH), exist_ok=True)
        with open(TEST_SCRIPT_PATH, 'w') as f:
            f.write(script_content)
        # Make script executable
        os.chmod(TEST_SCRIPT_PATH, 0o755)
        log.info(f"Test script created at {TEST_SCRIPT_PATH}")
        return True, "Test script generated successfully."
    except Exception as e:
        log.error(f"Failed to generate test script: {e}")
        return False, str(e)

def update_supervisor(board_command, update_check=False, debug=False):
    """Updates the scoreboard.conf supervisor file with the complete command."""
    gpio_slowdown = get_pi_model_slowdown()
    command_prefix = "command=/home/pi/nhlsb-venv/bin/python3 src/main.py "
    
    update_check_flag = " --updatecheck" if update_check else ""
    
    # Note: user mentioned command=/home/pi/nhlsb-venv/bin/python3 ./src/main.py in one place,
    # and "command=/home/pi/nhlsb-venv/bin/python3 src/main.py " in another. I will use the non-dot version
    # but add it to the prefix to match user exactly.
    sup_command = f"{command_prefix}{gpio_slowdown} {board_command}{update_check_flag}"

    if debug:
        log.info(f"Debug mode enabled: Skipping supervisor config update. Command would be: {sup_command}")
        return True, "Debug mode: Supervisor config update skipped."

    try:
        # delete the line with command=
        subprocess.run(['sudo', 'sed', '-i', '/command=/d', SUPERVISOR_CONF], check=True)
        # add the new command to scoreboard.conf (the user specified "/program/a $sup_command")
        subprocess.run(['sudo', 'sed', '-i', f'/program/a {sup_command}', SUPERVISOR_CONF], check=True)
        
        # Enable the service
        subprocess.run(['sudo', 'systemctl', 'enable', 'supervisor'], check=True)
        
        # Read the file contents as root to return to frontend
        conf_content = subprocess.check_output(['sudo', 'cat', SUPERVISOR_CONF]).decode('utf-8')
        
        log.info(f"Updated supervisor config with command: {sup_command}")
        return True, conf_content
    except subprocess.CalledProcessError as e:
        log.error(f"Failed to update supervisor config: {e}")
        return False, str(e)

def finish_onboarding():
    """Deletes the SETUP file."""
    try:
        if os.path.exists(SETUP_FILE):
            os.remove(SETUP_FILE)
            log.info("SETUP file deleted.")
        return True, "Onboarding finished."
    except Exception as e:
        log.error(f"Failed to delete SETUP file: {e}")
        return False, str(e)
