#!/bin/bash
# This script must be run as the postgres user
# e.g. sudo -u postgres ./createdb.sh
dropdb autoland
dropdb testautoland

dropuser autoland
createuser autoland -S -D -R

createdb autoland
psql autoland -f schema.sql

createdb testautoland
psql testautoland -f schema.sql
