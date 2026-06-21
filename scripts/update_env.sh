#!/bin/bash
# Environment loader
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi
