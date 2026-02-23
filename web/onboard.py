import os
import json
import subprocess
import logging

log = logging.getLogger(__name__)

STATUS_FILE = '/home/pi/.nhlledportal/status'
SETUP_FILE = '/home/pi/.nhlledportal/SETUP'
TEST_SCRIPT_PATH = '/home/pi/sbtools/testMatrix.sh'
SUPERVISOR_CONF = '/etc/supervisor/conf.d/scoreboard.conf'
CONFIGS_ZIP_PATHS = [
    '/boot/firmware/scoreboard/configs.zip',
    '/boot/scoreboard/configs.zip'
]

def get_configs_zip_path():
    for path in CONFIGS_ZIP_PATHS:
        if os.path.exists(path):
            return path
    return None

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
    """Generates the testMatrix.sh and splash.sh scripts using frontend values."""
    status_text = read_status_file()
    
    script_content = f"""#!/bin/bash
echo 'Watch your display for $(tput setaf 3){status_text}$(tput sgr0) to be displayed'
echo 'This will run for about 15 seconds and then exit itself'
cd /home/pi/nhl-led-scoreboard/submodules/matrix/bindings/python/samples
sudo /home/pi/nhlsb-venv/bin/python3 runtext.py {board_command} -y 20 -l 1 -C 255,255,0 -t '{status_text}' >/dev/null 2>&1
clear
exit
"""

    splash_content = f"""#!/bin/bash
cd /home/pi/sbtools/
./led-image-viewer {board_command} -t60 -C splash.gif
"""

    if debug:
        log.info("Debug mode enabled: Returning script content instead of creating file.")
        return True, script_content

    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(TEST_SCRIPT_PATH), exist_ok=True)
        
        # Write testMatrix.sh
        with open(TEST_SCRIPT_PATH, 'w') as f:
            f.write(script_content)
        # Make script executable
        os.chmod(TEST_SCRIPT_PATH, 0o755)
        log.info(f"Test script created at {TEST_SCRIPT_PATH}")
        
        # Write splash.sh
        splash_path = '/home/pi/sbtools/splash.sh'
        with open(splash_path, 'w') as f:
            f.write(splash_content)
        # Make script executable
        os.chmod(splash_path, 0o755)
        log.info(f"Splash script created at {splash_path}")
        
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
        # If the file exists, it might be the imported one which already has the correct command.
        # But if we want to add the custom command, we modify it.
        # Check if scoreboard.conf is already imported and we just want to enable it
        if board_command is None:
            # Service only enable (implies it's imported)
            subprocess.run(['sudo', 'systemctl', 'enable', 'supervisor'], check=False)
            conf_content = subprocess.check_output(['sudo', 'cat', SUPERVISOR_CONF]).decode('utf-8')
            log.info(f"Enabled supervisor service on existing config.")
            return True, conf_content

        # delete the line with command=
        subprocess.run(['sudo', 'sed', '-i', '/command=/d', SUPERVISOR_CONF], check=True)
        # add the new command to scoreboard.conf (the user specified "/program/a $sup_command")
        subprocess.run(['sudo', 'sed', '-i', f'/program/a {sup_command}', SUPERVISOR_CONF], check=True)
        
        # Enable the service
        subprocess.run(['sudo', 'systemctl', 'enable', 'supervisor'], check=False)
        
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

def check_configs_zip():
    """Checks if the configs.zip exists in the scoreboard directory."""
    return get_configs_zip_path() is not None

def import_configs_zip(version):
    """
    Imports configs.zip by unzipping it to a temporary directory 
    and moving the files to their proper places.
    """
    import tempfile
    import shutil
    import glob

    configs_zip = get_configs_zip_path()
    if not configs_zip:
        return False, "configs.zip not found."

    try:
        # Create temporary directory
        tmpdir = tempfile.mkdtemp()
        
        # Unzip configs.zip to tmpdir
        log.info(f"Unzipping {configs_zip} to {tmpdir}")
        subprocess.run(['unzip', '-o', configs_zip, '-d', tmpdir], check=True, stdout=subprocess.DEVNULL)

        # Iterate and copy
        # config.json
        config_src = os.path.join(tmpdir, 'config.json')
        if os.path.exists(config_src):
            subprocess.run(['sudo', 'cp', config_src, '/home/pi/nhl-led-scoreboard/config/config.json'], check=True)
            subprocess.run(['sudo', 'chown', 'pi:pi', '/home/pi/nhl-led-scoreboard/config/config.json'], check=True)

        # logos_*x*.json
        layout_files = glob.glob(os.path.join(tmpdir, 'logos_*x*.json'))
        for layout_file in layout_files:
            dest = f"/home/pi/nhl-led-scoreboard/config/layout/{os.path.basename(layout_file)}"
            subprocess.run(['sudo', 'cp', layout_file, dest], check=True)
            subprocess.run(['sudo', 'chown', 'pi:pi', dest], check=True)

        # logos folder
        logos_src = os.path.join(tmpdir, 'logos')
        if os.path.isdir(logos_src):
            subprocess.run(['sudo', 'cp', '-r', logos_src, '/home/pi/nhl-led-scoreboard/assets/'], check=True)
            subprocess.run(['sudo', 'chown', '-R', 'pi:pi', '/home/pi/nhl-led-scoreboard/assets/logos'], check=True)

        # testMatrix.sh
        test_src = os.path.join(tmpdir, 'testMatrix.sh')
        if os.path.exists(test_src):
            try:
                # Ensure it has the necessary formatting arguments for runtext.py
                with open(test_src, 'r') as f:
                    content = f.read()
                
                if 'runtext.py' in content:
                    # Look for lines containing runtext.py
                    lines = content.split('\n')
                    for i, line in enumerate(lines):
                        if 'runtext.py' in line:
                            # Add missing arguments if they aren't there
                            if '-y 20' not in line:
                                line = line.replace('runtext.py', 'runtext.py -y 20')
                            if '-l 1' not in line:
                                line = line.replace('runtext.py', 'runtext.py -l 1')
                            if '-C 255,255,0' not in line:
                                line = line.replace('runtext.py', 'runtext.py -C 255,255,0')
                            lines[i] = line
                    
                    content = '\n'.join(lines)
                    with open(test_src, 'w') as f:
                        f.write(content)
            except Exception as e:
                log.warning(f"Failed to update arguments in testMatrix.sh: {e}")

            subprocess.run(['sudo', 'cp', test_src, '/home/pi/sbtools/testMatrix.sh'], check=True)
            subprocess.run(['sudo', 'chmod', '+x', '/home/pi/sbtools/testMatrix.sh'], check=True)
            # Update to the latest version
            subprocess.run(['sudo', 'sed', '-i', '-E', f"/latest version/s/V[0-9]{{4}}\.[0-9]{{2}}\.[0-9]+/{version}/", '/home/pi/sbtools/testMatrix.sh'], check=True)
            # Note: The user mentioned "do_test_matrix" which in bash is likely running the test matrix or just setting a flag.
            # We don't run the matrix here, the user manually runs to test it in the UI.

        # splash.sh
        splash_src = os.path.join(tmpdir, 'splash.sh')
        if os.path.exists(splash_src):
            subprocess.run(['sudo', 'cp', splash_src, '/home/pi/sbtools/splash.sh'], check=True)
            subprocess.run(['sudo', 'chmod', '+x', '/home/pi/sbtools/splash.sh'], check=True)

        # scoreboard.conf
        conf_src = os.path.join(tmpdir, 'scoreboard.conf')
        if os.path.exists(conf_src):
            subprocess.run(['sudo', 'cp', conf_src, '/etc/supervisor/conf.d/scoreboard.conf'], check=True)
            subprocess.run(['sudo', 'mkdir', '-p', '/home/pi/config_backup'], check=True)
            subprocess.run(['sudo', 'mv', configs_zip, '/home/pi/config_backup/'], check=True)
            subprocess.run(['sudo', 'chown', '-R', 'pi:pi', '/home/pi/config_backup'], check=True)

        return True, "Import complete."

    except subprocess.CalledProcessError as e:
        log.error(f"Subprocess failed during import: {e}")
        return False, f"Import error: {e}"
    except Exception as e:
        log.error(f"Error during configs.zip import: {e}")
        return False, str(e)
    finally:
        # Cleanup tmpdir
        if os.path.exists(tmpdir):
            shutil.rmtree(tmpdir, ignore_errors=True)
