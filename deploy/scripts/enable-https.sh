#!/usr/bin/env bash
# Obtains a Let's Encrypt certificate via certbot's HTTP-01 challenge and
# switches nginx over to the HTTPS config. Requires DNS already pointed
# at this box (an A record → the Elastic IP — see
# infra/terraform/envs/single-ec2's route53 outputs if Terraform manages
# it, or set the record by hand).
#
# Usage: deploy/scripts/enable-https.sh <domain> <email>
set -euo pipefail

if [ $# -ne 2 ]; then
  echo "Usage: $0 <domain> <email>" >&2
  exit 1
fi

DOMAIN="$1"
EMAIL="$2"

cd /opt/personal-finance-platform

echo "Requesting a certificate for $DOMAIN..."
docker run --rm \
  -v personal-finance-platform_certbot_www:/var/www/certbot \
  -v personal-finance-platform_certbot_certs:/etc/letsencrypt \
  certbot/certbot certonly --webroot \
  -w /var/www/certbot \
  -d "$DOMAIN" \
  --email "$EMAIL" \
  --agree-tos \
  --non-interactive

echo "Rendering the HTTPS nginx config..."
DOMAIN="$DOMAIN" envsubst '${DOMAIN}' \
  < deploy/nginx/nginx-ssl.conf.template \
  > deploy/nginx/nginx.conf

echo "Restarting nginx..."
docker compose -f deploy/docker-compose.prod.yml restart nginx

echo "HTTPS enabled for $DOMAIN."
echo ""
echo "Certificates expire every 90 days. Add this to cron for renewal:"
echo "  0 4 * * 0 docker run --rm -v personal-finance-platform_certbot_www:/var/www/certbot -v personal-finance-platform_certbot_certs:/etc/letsencrypt certbot/certbot renew --webroot -w /var/www/certbot && docker compose -f /opt/personal-finance-platform/deploy/docker-compose.prod.yml restart nginx"
