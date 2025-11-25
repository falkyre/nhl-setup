# NHL LED Scoreboard - Control Hub

This is the web-based control hub for the NHL LED Scoreboard project. It provides a user-friendly interface to configure the scoreboard, manage plugins, and view logs.

### History
* V2025.12.0 - December 1, 2025

### Latest Changes

#### V2025.12.0
Initial release.  This is fully experimental web ui to handle the main configuration, a web front end for installing plugins, a utilities page (currently only has the issue upload utility), and a supervisor page if you are using supervisor.

## Installation

### Debian Package (Recommended)

The easiest way to install the control hub is via the Debian package. This will install the control hub as a systemd service that starts on boot.  The base install does require configuration prior to enabling and starting the systemd unit.  See the configuration section below.

1.  Download the latest `.deb` package from the [releases page](https://github.com/falkyre/nhl-setup/releases).
2.  Install the package:

    ```bash
    sudo dpkg -i nls-controlhub_*.deb
    ```

3.  Enable and start the service:

    ```bash
    sudo systemctl enable nls_controlhub
    sudo systemctl start nls_controlhub
    ```

The control hub will now be running on port 8000. You can access it at `http://<your_pi_ip>:8000`.

### Manual Installation

1.  Clone the repository:

    ```bash
    git clone https://github.com/falkyre/nhl-setup.git
    cd nhl-setup/web
    ```

2.  Install the dependencies:

    ```bash
    pip install -r requirements.txt
    ```

3.  Run the server:

    ```bash
    python3 config_server.py
    ```

## Configuration

The control hub can be configured via a TOML file.

### Debian Package

When installed via the Debian package, the configuration file is located at `/etc/nls_controlhub/config.toml`.

### Manual Installation

When running manually, the server looks for a `config.toml` file in the same directory as `config_server.py`. You can also specify a path to a config file using the `--config` command-line option.

### Options

The following options are available in `config.toml`:

| Option            | Description                                                                                             | Default                                |
| ----------------- | ------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| `PORT`            | The port the web server will run on.                                                                    | `8000`                                 |
| `PYTHON_EXEC`     | The path to the python executable in your scoreboard's virtual environment.                             | `python3`                              |
| `SUPERVISOR_URL`  | The URL of the supervisor XML-RPC interface.                                                            | `127.0.0.1`                            |
| `SUPERVISOR_PORT` | The port of the supervisor XML-RPC interface.                                                           | `9001`                                 |
| `scoreboard_dir`  | Path to the root of the `nhl-led-scoreboard` directory (where `VERSION` and `plugins.json` are located). | `.`                                    |

**Example `config.toml`:**

```toml
# /etc/nls_controlhub/config.toml

# The port the web server will run on.
PORT = 8000

# The path to the python executable in your scoreboard's virtual environment.
PYTHON_EXEC = "/home/pi/nhlsb-venv/bin/python3"

# Path to the root of the nhl-led-scoreboard directory.
# This is where the main scoreboard code is located.
scoreboard_dir = "/home/pi/nhl-led-scoreboard"
```

## Command-line Options

The following command-line options are available when running `config_server.py` manually:

| Option                     | Description                                                                                                    | Default                                |
| -------------------------- | -------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| `-d`, `--scoreboard_dir`   | Path to the root of the `nhl-led-scoreboard` directory. This overrides the value in the config file.             | `None`                                 |
| `--config`                 | Path to the TOML configuration file.                                                                           | `config.toml` in the script directory. |
| `--debug`                  | Run Flask in debug mode.                                                                                       | `False`                                |
| `-v`, `--version`          | Show the version of the control hub.                                                                           |                                        |

## Running the Server

### As a Service (Debian Package)

If you installed the Debian package, the server runs as a `systemd` service.

*   **Start the server:** `sudo systemctl start nls_controlhub`
*   **Stop the server:** `sudo systemctl stop nls_controlhub`
*   **View logs:** `journalctl -u nls_controlhub -f`

### Systemd Service (Unit File)

The Debian package installs a `systemd` unit file at `/lib/systemd/system/nls_controlhub.service`. This file controls how the control hub runs as a service.

The default unit file looks like this:

```ini
[Unit]
Description=NLS Control Hub
After=network.target

[Service]
ExecStart=/usr/local/bin/nls_controlhub --config /etc/nls_controlhub/config.toml
User=pi
WorkingDirectory=/home/pi
Restart=on-failure
RestartSec=5
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=nls_controlhub

[Install]
WantedBy=multi-user.target
```

**Important:** The `User` and `WorkingDirectory` are set to `pi` and `/home/pi` by default. If you are using a different username on your Raspberry Pi (as is default on newer Raspberry Pi OS versions like Bookworm and Trixie), you will need to edit this file.  Set the `WorkingDirectory` to be the location of where you installed the nhl-led-scoreboard.

1.  Open the file for editing: `sudo systemctl edit --full nls_controlhub.service`
2.  Change the `User` and `WorkingDirectory` to match your username and home directory.
3.  Save the file and reload the `systemd` daemon: `sudo systemctl daemon-reload`
4.  Restart the service: `sudo systemctl restart nls_controlhub`

### Non-Standard Installations

The default configuration is designed for a standard installation of the NHL LED Scoreboard project (e.g., using the provided image or the standard installation scripts). This assumes that the scoreboard is located at `/home/pi/nhl-led-scoreboard` and the virtual environment is at `/home/pi/nhlsb-venv`.

If you have a non-standard installation (e.g., different directory paths, different user), you will need to modify the configuration to match your setup. The primary settings to change are in `/etc/nls_controlhub/config.toml` and the `nls_controlhub.service` file. By adjusting the `scoreboard_dir`, `PYTHON_EXEC`, `User`, and `WorkingDirectory` options, you should be able to get the control hub working with your setup.

### Manually

To run the server manually:

```bash
python3 config_server.py
```

You can then access the control hub at `http://<your_ip>:8000`.
