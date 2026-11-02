#!/bin/sh
# Install Oracle Instant Client (Basic Light) into /opt/oracle/instantclient. Run by docker/Dockerfile; stops at the
# first error (the Dockerfile then warns and carries on without it). Env: ORACLE_CLIENT_URL.
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends unzip libaio1 || apt-get install -y --no-install-recommends unzip libaio1t64
rm -rf /tmp/oc /opt/oracle/instantclient; mkdir -p /opt/oracle /tmp/oc
zip=$(ls /src/oracle-client/*.zip 2>/dev/null | head -1 || true)
if [ -z "$zip" ]; then
  zip=/tmp/ic.zip
  python3 -c "import sys,urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" "$ORACLE_CLIENT_URL" "$zip"
fi
unzip -q -o "$zip" -d /tmp/oc
d=$(find /tmp/oc -maxdepth 1 -type d -name 'instantclient*' | head -1)
[ -n "$d" ] || { echo "no instantclient folder in the zip" >&2; exit 1; }
mv "$d" /opt/oracle/instantclient
ls /opt/oracle/instantclient/libclntsh.so* >/dev/null
if ! ldconfig -p | grep -q 'libaio.so.1 '; then
  a=$(ls /usr/lib/*/libaio.so.1t64 2>/dev/null | head -1 || true)
  [ -z "$a" ] || ln -sf "$a" /opt/oracle/instantclient/libaio.so.1
fi
echo /opt/oracle/instantclient > /etc/ld.so.conf.d/oracle-instantclient.conf
ldconfig
chmod -R a+rX /opt/oracle
echo "Oracle Instant Client installed"
