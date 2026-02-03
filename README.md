# NLS Control Hub APT Repository

This repository hosts the Debian packages for the **NHL LED Scoreboard Control Hub**.

## Installation Instructions

Follow these steps to add this repository to your Raspberry Pi and install the control hub.

### Method 1: Automatic Setup (Recommended)

Download and install the repository configuration package. This will automatically set up the GPG key and repository references.
```bash
# 1. Download the setup package
wget "https://falkyre.github.io/repo/nls-controlhub-apt-source_2026.02.0_all.deb"

# 2. Install it

sudo dpkg -i "nls-controlhub-apt-source_2026.02.0_all.deb"

# 3. Update and Install Control Hub
sudo apt update
sudo apt install nls-controlhub
```
         
### Method 2: Manual Setup

If you prefer to configure it manually:

### 1. Install GPG Key and repository source
```bash
# Download and install the GPG key
curl -s --compressed "https://falkyre.github.io/nhl-setup/repo/KEY.gpg" | gpg --dearmor | sudo tee /etc/apt/trusted.gpg.d/nls-controlhub.gpg > /dev/null

# Add the repository to your sources list
echo "deb [signed-by=/etc/apt/trusted.gpg.d/nls-controlhub.gpg] https://falkyre.github.io/nhl-setup/repo ./" | sudo tee /etc/apt/sources.list.d/nls-controlhub.list
```

### 2. Install the Package

Update your package list and install the control hub:

```bash
sudo apt update
sudo apt install nls-controlhub
```

---

## Configuration

**Before starting the service**, you must ensure it is configured to run as the correct user and look in the correct directory for your scoreboard installation.

### 1. Set User and Working Directory

The service relies on a default configuration file located at `/etc/default/nls_controlhub`.

1.  Open the file for editing:
    ```bash
    sudo nano /etc/default/nls_controlhub
    ```

2.  Update the `User` and `WorkingDirectory` variables to match your installation.
    * **User**: The username you use on your Raspberry Pi (e.g., `pi`, `dietpi`, or your custom user).
    * **WorkingDirectory**: The full path to your `nhl-led-scoreboard` directory.

    **Example:**
    ```ini
    # Configuration for NLS Control Hub Service
    
    # User to run the service as
    User=myuser
    
    # Directory where nhl-led-scoreboard is installed
    WorkingDirectory=/home/myuser/nhl-led-scoreboard
    ```

3.  Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).

### 2. Apply Changes and Start Service

Once you have updated the defaults, re-run the package configuration to generate the correct systemd unit file and start the service:

```bash
sudo dpkg-reconfigure nls-controlhub
```

*Note: The service should start automatically after this step. You can check its status with:*

```bash
sudo systemctl status nls_controlhub
```
