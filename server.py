import json
import yaml
import asyncio
import argparse
import tornado.web
from tornado.httpclient import AsyncHTTPClient
import ipaddress
from subprocess import Popen, PIPE
from datetime import datetime, timedelta
birdlg_proxies = []
response_cache = {}
cache_timeout = 1

def extract_member(base_json):
    return list(map(lambda x:x["value"],filter(lambda x:x["name"] == "members", base_json["objects"]["object"][0]["attributes"]["attribute"])))
def pack_member(base_json,member_list):
    atlist = base_json["objects"]["object"][0]["attributes"]["attribute"]
    atlist = list(filter(lambda x:x["name"] != "members",atlist))
    atlist = atlist[0:3] + [{"name": "members", "value": member, "referenced-type":"aut-num" if member[:2] == "AS" and member[2:].isdecimal() else "as-set" } for member in member_list] + atlist[3:]
    base_json["objects"]["object"][0]["attributes"]["attribute"] = atlist
    return base_json
def getval(strin):
    return strin.split(":",1)[1].strip()

def getAddr(addr):
    addr = addr.strip()
    if "%" in addr:
        addr = addr.split("%",1)
    else:
        addr = addr, None
    return ipaddress.ip_address(addr[0]) , addr[1]

def getAddrFromChannel(birdspaline):
    birdspaline = birdspaline.strip()
    if " " in birdspaline:
        addr,ll = birdspaline.split(" ",1)
        if addr == "::":
            return ipaddress.ip_address(ll)
        return ipaddress.ip_address(addr)
    return ipaddress.ip_address(birdspaline)

def getroutecount(birdspaline):
    birdspaline = birdspaline.strip()
    infos_list = list( map( lambda x:x.strip(), birdspaline.split(",")))
    infos =  {"imported": 0,"filtered":0,"exported": 0,"preferred": 0}
    for info in infos_list:
        val,key = info.strip().split(" ")
        val = int(val)
        infos[key] = val
    return infos

def get_bird_session(n="*",birdc_output = None):
    if n == "*":
        n = '"*"'
    if birdc_output == None:
        birdc_output = Popen(["birdc", "s", "p","a",n], stdin=PIPE, stdout=PIPE).communicate()[0].decode()
    birdc_output = birdc_output.split("\n")[2:]
    birdc_output = "\n".join(birdc_output).split("\n\n")
    result_list = []
    for proto_str in birdc_output:
        proto_str_line = proto_str.split("\n")
        protoinfo = proto_str_line[0].strip().split()
        if len(protoinfo) < 3:
            continue
        proto_name, proto_type, proto_table ,proto_state , proto_since ,proto_info = protoinfo[0] , protoinfo[1], protoinfo[2], protoinfo[3], protoinfo[4], protoinfo[-1]
        if proto_type != "BGP":
            continue
        result = {"name": proto_name, "state":None, "as": {"local":0, "remote":0}, "addr":{"af": 0, "local":None, "remote":None, "interface":None}, "route":{"ipv4":{"imported":0,"filtered":0,"exported":0,"preferred":0},"ipv6":{"imported":0,"filtered":0,"exported":0,"preferred":0}}}
        current_channel = ""
        for L in proto_str_line:
            if "BGP state:" in L:
                result["state"] = getval(L)
            elif "Neighbor AS:" in L:
                result["as"]["remote"] = int(getval(L))
            elif "Local AS" in L:
                result["as"]["local"] = int(getval(L))
            elif "Neighbor address:" in L:
                remote = getval(L)
                addrobj,interface = getAddr(remote)
                result["addr"]["interface"] = interface
                result["addr"]["remote"] = str(addrobj)
                if type(addrobj) == ipaddress.IPv4Address:
                    result["addr"]["af"] = 4
                elif type(addrobj) == ipaddress.IPv6Address:
                    result["addr"]["af"] = 6
            elif "Channel" in L:
                current_channel = L.split("Channel ")[1].strip()
            elif "Routes:" in L:
                result["route"][current_channel] = getroutecount(getval(L))
            elif "BGP Next hop:" in L:
                if (result["addr"]["af"] == 4 and current_channel == "ipv4") or (result["addr"]["af"] == 6 and current_channel == "ipv6"):
                    result["addr"]["local"] = str(getAddrFromChannel(getval(L)))
        result_list += [result]
    #return yaml.safe_dump(result_list)
    return result_list

def get_birdc_output(n="*",sockpath=None):
    if n == "*":
        n = '"*"'
    if sockpath == None or sockpath == "":
        return Popen(["birdc", "-r", "s", "p","a",n], stdin=PIPE, stdout=PIPE).communicate()[0].decode()
    else:
        return Popen(["birdc","-s",sockpath, "-r", "s", "p","a",n], stdin=PIPE, stdout=PIPE).communicate()[0].decode()
     
    

class BIRDHandler(tornado.web.RequestHandler):

    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "x-requested-with")
        self.set_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.set_header('Cache-Control','public, max-age=' + str(cache_timeout))
    async def get(self):
        remote_ip = self.request.headers.get("X-Real-IP") or \
            self.request.headers.get("X-Forwarded-For") or \
            self.request.remote_ip
        birdlg_proxy = self.get_argument("RS", "", True)
        print(remote_ip + "\t" + birdlg_proxy,end="\t")
        if birdlg_proxy in response_cache and datetime.now() - response_cache[birdlg_proxy]["time"] < timedelta(seconds=cache_timeout):
            self.write(response_cache[birdlg_proxy]["response"])
            print("cached")
            return
        print("")
        if birdlg_proxy not in birdlg_proxies:
            raise tornado.web.HTTPError(404,"[]")
        proxy_url = birdlg_proxies[birdlg_proxy]
        if proxy_url == "" or proxy_url[0] == "/":
            birdc_output = get_birdc_output(sockpath=proxy_url)
        if proxy_url.startswith("http"):
            birdc_output_response = await AsyncHTTPClient().fetch(proxy_url + "/bird?q=show+protocols+all")
            birdc_output = birdc_output_response.body.decode("utf8")
        bird_session = get_bird_session(birdc_output = birdc_output)
        response_cache[birdlg_proxy] = {"response":json.dumps(bird_session),"time":datetime.now()}
        self.write(response_cache[birdlg_proxy]["response"])

def make_app(urlpath):
    return tornado.web.Application([
        (urlpath + "bird", BIRDHandler),
    ])

async def main(urlpath,port):
    app = make_app(urlpath)
    app.listen(port)
    await asyncio.Event().wait()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8888, nargs='?', help="HTTP(s) server port number")
    parser.add_argument("--urlpath", type=str, default="/", nargs='?', help="HTTP(s) server url path")
    parser.add_argument("--cache_timeout", type=int, default=1, nargs='?', help="Http Cache Timeout")
    parser.add_argument("--birds", type=str, default=None, nargs='?', help="Bird lg proxy instences",action='append')
    
    args = parser.parse_args()
    cache_timeout = args.cache_timeout
    if args.birds == None:
        args.birds = [":"]
    birdlg_proxies = { k:v for k,v in map(lambda x:x.split(":",1),args.birds)}
    asyncio.run(main(args.urlpath,args.port))
