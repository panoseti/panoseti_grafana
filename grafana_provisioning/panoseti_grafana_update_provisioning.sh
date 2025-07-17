#! /bin/bash

DASHBOARD_PATH=/etc/grafana/provisioning/dashboards
DATASOURCE_PATH=/etc/grafana/provisioning/datasources

rm -r $DASHBOARD_PATH
rm -r $DATASOURCE_PATH

cp -r ./dashboards $DASHBOARD_PATH
cp -r ./datasources $DATASOURCE_PATH