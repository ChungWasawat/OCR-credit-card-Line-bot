#!/bin/bash

# Define the path to your .env file
ENV_FILE=".env"

# Check if the .env file exists
if [ -f "$ENV_FILE" ]; then
    echo "Loading variables from $ENV_FILE..."
    
    # 1. Automatically export all variables defined from this point onward
    set -a
    
    # 2. Filter out comments/empty lines and source the valid lines into the shell
    source <(grep -v '^#' "$ENV_FILE" | grep -v '^$')
    
    # 3. Disable the automatic export feature
    set +a
else
    echo "Error: $ENV_FILE file not found!"
    exit 1
fi

# Example usage: accessing the variables
echo "Sheet ID: $SHEET_ID"
echo "Drive ID: $DRIVE_FOLDER_ID"