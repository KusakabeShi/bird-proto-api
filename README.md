# bird-proto-api

first: setup this proxy at all your route servers: https://github.com/xddxdd/bird-lg-go

```usage
python3 server.py --port 3236 \
    --bird "RS1:http://[2404:f4c0:f70e:1980::1:1]:3234" \
    --bird "RS2:http://[2404:f4c0:f70e:1980::2:1]:3234" \
    --bird "RS2:http://[2404:f4c0:f70e:1980::3:1]:3234"
```
