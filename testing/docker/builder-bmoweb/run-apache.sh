#!/bin/bash

set -e

. /etc/apache2/envvars

exec /usr/sbin/apache2 -DFOREGROUND
