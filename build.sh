#!/bin/bash
faas-cli build \
    -f instaloader.yml \
    --build-arg ADDITIONAL_PACKAGE='gcc musl-dev python3-dev libffi-dev openssl-dev cargo'

