Optional: Oracle Instant Client for the Docker image (thick mode, needed when the Oracle database requires
Native Network Encryption — error DPY-3001).

By default docker-build downloads "Basic Light" (Linux x86-64) from oracle.com. If the build machine cannot reach
oracle.com, download instantclient-basiclite-linux.x64-*.zip yourself
(https://www.oracle.com/database/technologies/instant-client/linux-x86-64-downloads.html) and put the .zip in
this folder before running scripts/docker-build.sh. Keep only one zip here.
