#!/bin/bash
# This script installs Node.js v18.17.1 on a Raspberry Pi Zero W (ARMv6 architecture).
# Ensures compatibility with Homey CLI and resolves engine warnings.

VERSION=v18.17.1;

# Create a directory for downloads and navigate to it
cd ~/ && mkdir temp && cd temp;

# Download the Node.js ARMv6 build for the specified version
wget https://unofficial-builds.nodejs.org/download/release/$VERSION/node-$VERSION-linux-armv6l.tar.gz;

# Extract the downloaded tarball
tar -xzf node-$VERSION-linux-armv6l.tar.gz;

# Remove the tarball after extraction
sudo rm node-$VERSION-linux-armv6l.tar.gz;

# Remove any existing Node.js installation
sudo rm -rf /opt/nodejs;

# Move the extracted Node.js files to the appropriate directory
sudo mv node-$VERSION-linux-armv6l /opt/nodejs/;

# Remove existing symlinks for node, npm, and npx
sudo unlink /usr/bin/node 2>/dev/null;
sudo unlink /usr/sbin/node 2>/dev/null;
sudo unlink /sbin/node 2>/dev/null;
sudo unlink /usr/local/bin/node 2>/dev/null;
sudo unlink /usr/bin/npm 2>/dev/null;
sudo unlink /usr/sbin/npm 2>/dev/null;
sudo unlink /sbin/npm 2>/dev/null;
sudo unlink /usr/local/bin/npm 2>/dev/null;
sudo unlink /usr/bin/npx 2>/dev/null;
sudo unlink /usr/sbin/npx 2>/dev/null;
sudo unlink /sbin/npx 2>/dev/null;
sudo unlink /usr/local/bin/npx 2>/dev/null;

# Create new symlinks for node, npm, and npx
sudo ln -s /opt/nodejs/bin/node /usr/bin/node;
sudo ln -s /opt/nodejs/bin/node /usr/sbin/node;
sudo ln -s /opt/nodejs/bin/node /sbin/node;
sudo ln -s /opt/nodejs/bin/node /usr/local/bin/node;
sudo ln -s /opt/nodejs/bin/npm /usr/bin/npm;
sudo ln -s /opt/nodejs/bin/npm /usr/sbin/npm;
sudo ln -s /opt/nodejs/bin/npm /sbin/npm;
sudo ln -s /opt/nodejs/bin/npm /usr/local/bin/npm;
sudo ln -s /opt/nodejs/bin/npx /usr/bin/npx;
sudo ln -s /opt/nodejs/bin/npx /usr/sbin/npx;
sudo ln -s /opt/nodejs/bin/npx /sbin/npx;
sudo ln -s /opt/nodejs/bin/npx /usr/local/bin/npx;

# Clean up the temporary directory
cd ~ && rm -rf ~/temp;

# Verify the installation
echo "Node.js version installed:"
node --version
echo "npm version installed:"
npm --version
