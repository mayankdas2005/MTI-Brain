#!/bin/bash -x
set -euo pipefail

PGB_DIR="/home/pgbouncer"
INI="${PGB_DIR}/pgbouncer.ini"
USERLIST="${PGB_DIR}/userlist.txt"

NUM_BACKENDS="${REDSHIFT_NUM_BACKENDS:-1}"
PGB_ADMIN_USERS="${PGB_ADMIN_USERS:-${REDSHIFT_USER:?REDSHIFT_USER is required}}"
PGB_ADMIN_PASSWORDS="${PGB_ADMIN_PASSWORDS:-${REDSHIFT_PASSWORD:?REDSHIFT_PASSWORD is required}}"

# Auto-generate ini if it doesn't exist
if [ ! -f "${INI}" ]; then

  # Build [databases] section.
  # The wildcard "*" catches any db name the client uses (e.g. "dev").
  # routing_rules.py then returns "redshift_N" and pgbouncer re-resolves
  # to the named entry for the actual backend connection.
  DEFAULT_HOST_VAR="REDSHIFT_HOST_0"
  DEFAULT_HOST="${!DEFAULT_HOST_VAR:-${REDSHIFT_HOST:?REDSHIFT_HOST or REDSHIFT_HOST_0 is required}}"
  DATABASES_BLOCK="    * = host=${DEFAULT_HOST} port=${REDSHIFT_PORT:-5439} dbname=${REDSHIFT_DB:?REDSHIFT_DB is required} user=${REDSHIFT_USER} password=${REDSHIFT_PASSWORD}"$'\n'

  for i in $(seq 0 $((NUM_BACKENDS - 1))); do
    HOST_VAR="REDSHIFT_HOST_${i}"
    HOST="${!HOST_VAR:-${REDSHIFT_HOST}}"
    DATABASES_BLOCK+="    redshift_${i} = host=${HOST} port=${REDSHIFT_PORT:-5439} dbname=${REDSHIFT_DB} user=${REDSHIFT_USER} password=${REDSHIFT_PASSWORD}"$'\n'
  done

  # Readonly pool — connects to Redshift using the SELECT-only user.
  # Backend connects with dbname=readonly to route through this entry.
  REDSHIFT_READONLY_USER="${REDSHIFT_READONLY_USER:-}"
  REDSHIFT_READONLY_PASSWORD="${REDSHIFT_READONLY_PASSWORD:-}"
  if [ -n "${REDSHIFT_READONLY_USER}" ]; then
    DATABASES_BLOCK+="    readonly = host=${DEFAULT_HOST} port=${REDSHIFT_PORT:-5439} dbname=${REDSHIFT_DB} user=${REDSHIFT_READONLY_USER} password=${REDSHIFT_READONLY_PASSWORD}"$'\n'
  fi

  cat <<- END > "${INI}"
[databases]
${DATABASES_BLOCK}
[pgbouncer]
    listen_port = ${PGB_LISTEN_PORT:-5432}
    listen_addr = ${PGB_LISTEN_ADDR:-0.0.0.0}
    auth_type = md5
    pool_mode = ${PGBOUNCER_RR_POOL_MODE:-session}
    default_pool_size = ${PGBOUNCER_RR_DEFAULT_POOL_SIZE:-10}
    max_client_conn = ${PGBOUNCER_RR_MAX_CLIENT_CONN:-100}
    min_pool_size = ${PGBOUNCER_RR_MIN_POOL_SIZE:-2}
    reserve_pool_size = ${PGBOUNCER_RR_RESERVE_POOL_SIZE:-5}
    reserve_pool_timeout = ${PGBOUNCER_RR_RESERVE_POOL_TIMEOUT:-3}
    server_tls_sslmode = require
    server_idle_timeout = ${PGBOUNCER_RR_SERVER_IDLE_TIMEOUT:-600}
    client_idle_timeout = ${PGBOUNCER_RR_CLIENT_IDLE_TIMEOUT:-1800}
    ignore_startup_parameters = extra_float_digits,application_name,options,driver_version,os_version,plugin_name,os_user_agent,client_protocol_version,driver_name
    routing_rules_py_module_file = /home/pgbouncer/routing_rules.py
    log_connections = ${PGBOUNCER_RR_LOG_CONNECTIONS:-1}
    log_disconnections = ${PGBOUNCER_RR_LOG_DISCONNECTIONS:-1}
    log_pooler_errors = 1
    log_stats = ${PGBOUNCER_RR_LOG_STATS:-1}
    stats_period = ${PGBOUNCER_RR_STATS_PERIOD:-60}
    auth_file = ${USERLIST}
    logfile = ${PGB_DIR}/pgbouncer.log
    pidfile = ${PGB_DIR}/pgbouncer.pid
    admin_users = ${PGB_ADMIN_USERS}
END

  cat "${INI}"
fi

# Auto-generate userlist if it doesn't exist
if [ ! -f "${USERLIST}" ]; then
  IFS=',' read -ra admin_array <<< "${PGB_ADMIN_USERS}"
  IFS=',' read -ra password_array <<< "${PGB_ADMIN_PASSWORDS}"

  if (( ${#admin_array[@]} != ${#password_array[@]} )); then
    echo "Error: PGB_ADMIN_USERS and PGB_ADMIN_PASSWORDS have different lengths"
    exit 1
  fi

  for (( i=0; i < ${#admin_array[*]}; ++i )); do
    echo "\"${admin_array[$i]}\" \"${password_array[$i]}\"" >> "${USERLIST}"
  done
fi

chmod 0600 "${INI}"
chmod 0600 "${USERLIST}"

rm -f "${PGB_DIR}/pgbouncer.pid"

exec pgbouncer "${INI}"
