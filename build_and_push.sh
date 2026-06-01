#!/bin/sh
set -eu

REGISTRY="${REGISTRY:-ghcr.io/eu-farmbook}"
APP_IMAGE="${APP_IMAGE:-$REGISTRY/pagesense}"
TAG="${1:-latest}"

echo "Building app image: ${APP_IMAGE}:${TAG}"
docker build -t "${APP_IMAGE}:${TAG}" .

echo "Pushing app image: ${APP_IMAGE}:${TAG}"
docker push "${APP_IMAGE}:${TAG}"

echo "Done."
echo "App image: ${APP_IMAGE}:${TAG}"
