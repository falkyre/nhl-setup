import argparse
import sys
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    # Print 'no' if library is missing, or print helpful error to stderr
    sys.stderr.write("Error: 'paho-mqtt' library not found. Install with: pip install paho-mqtt\n")
    print("no")
    sys.exit(1)

def on_connect(client, userdata, flags, rc, properties=None):
    """
    Callback for when the client receives a CONNACK response from the server.
    rc = 0 means connection successful.
    """
    userdata['finished'] = True
    if rc == 0:
        userdata['status'] = "yes"
    else:
        userdata['status'] = "no"

def main():
    parser = argparse.ArgumentParser(description='Test MQTT Connectivity stub')
    parser.add_argument('broker', type=str, help='Broker address (e.g., localhost or 192.168.1.50)')
    parser.add_argument('port', type=int, help='Broker port (e.g., 1883)')
    parser.add_argument('-u', '--username', type=str, help='MQTT Username', default=None)
    parser.add_argument('-p', '--password', type=str, help='MQTT Password', default=None)

    args = parser.parse_args()

    # Dictionary to share state between main thread and callback
    connection_state = {'status': "no", 'finished': False}

    try:
        # Initialize client. 
        # Note: We are using a basic initialization compatible with most paho versions.
        # If using paho-mqtt 2.0+, it might emit a warning about CallbackAPIVersion, 
        # but will still function for this test.
        client = mqtt.Client(userdata=connection_state)
        
        client.on_connect = on_connect

        if args.username and args.password:
            client.username_pw_set(args.username, args.password)

        # Attempt connection
        client.connect(args.broker, args.port, keepalive=10)
        
        # Start the network loop in a background thread
        client.loop_start()

        # Wait up to 5 seconds for the on_connect callback
        timeout = 5
        start_time = time.time()
        
        while not connection_state['finished']:
            if time.time() - start_time > timeout:
                break
            time.sleep(0.1)

        client.loop_stop()
        client.disconnect()

        # Output the result
        print(connection_state['status'])

    except Exception:
        # Catch DNS errors, connection refused errors, etc.
        print("no")

if __name__ == "__main__":
    main()