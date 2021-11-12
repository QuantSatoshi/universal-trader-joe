import json
from datetime import datetime

import time
from collections import namedtuple
import sys
for x in sys.path:
    print(x)
sys.path.append('.pyenv/versions/3.7.6/lib/python3.7/site-packages/')
import stomp
import numpy as np
from jsmin import jsmin


conn = stomp.Connection([("node2.hawkguide.com", 64613)])
proj_config_file=r"../jobconfigs/config_bybit.json"
parm_file=r"./parms.json"

Trade = namedtuple("Trade", "price size side time")
Quote = namedtuple("Quote", "bid_price bid_size ask_price ask_size com time")

def center_of_mass(ask_vec,bid_vec,mid,decay):
        print("com")
        ask_mass = 0
        bid_mass = 0
        for q in ask_vec:
            ask_mass += q[1] * (decay ** (q[0] - mid))
        for q in bid_vec:
            bid_mass += q[1] * (decay ** (mid - q[0]))
        print(np.round(bid_mass - ask_mass, 6))
        return  np.round(bid_mass - ask_mass, 6)


def connect_and_subscribe(conn):
    conn.connect("admin", "Asj874hsaWE38jdsF", wait=True)
    conn.subscribe(
        destination="/topic/obstream/binance_fx/USD_BTC_perpetual_swap",
        id=1,
        ack="auto",
    )
    conn.subscribe(
        destination="/topic/tf/binance_fx/USD_BTC_perpetual_swap", id=1, ack="auto"
    )

## to send a prediction
# conn.send('/topic/prediction-1', '0.125', transaction=2)

EPS = 0.000001

def parse_config_file(config_file: str):
    with open(config_file, "r") as f:
        content = jsmin(f.read().strip())
    parsed_config = json.loads(content)
    return parsed_config


proj_config=parse_config_file(proj_config_file)
fitter_name=proj_config["real_time_fitter_name"]
ind_config={}
for f in proj_config["fitters"]:
    if f["name"]==fitter_name:
        ind_config=f
parms=parse_config_file(parm_file)

base_indicators=ind_config['base_indicators']

derived_indicator_configs=ind_config['derived_indicators']
derived_indicator_configs=[d for d in derived_indicator_configs if d["name"] not in ind_config["inds_unneeded_for_te"] ]
coeffs=parms['coefs']
means=parms['means']
outliers=parms['outliers']

class Indicator:
    def __init__(self,ind_config):
        self.name=ind_config['name']
        self.values=[]

    def update(self,trader):
        if(self.name=="trade_price"):
            self.values.append(trader.last_trade_price)
        elif(self.name=="trade_size"):
            self.values.append(trader.last_trade_size)
        elif(self.name=="trade_side"):
            side=0
            if(trader.last_trade_side=="b"):
                side=1
            elif(trader.last_trade_side=="s"):
                side=-1
            self.values.append(side)
        elif (self.name == "bid_price"):
            self.values.append(trader.last_bid_price)
        elif (self.name == "bid_size"):
            self.values.append(trader.last_bid_size)
        elif(self.name=="ask_price"):
            self.values.append(trader.last_ask_price)
        elif(self.name=="ask_size"):
            self.values.append(trader.last_ask_size)
        elif(self.name=="mid_price"):
            self.values.append((trader.last_bid_price+trader.last_ask_price)*0.5)
        else:
            self.values.append(0)
        print(self.name," ",self.values[-1])

class DerivedIndicator(Indicator):
    def __init__(self,ind_config):
        super().__init__(ind_config)
        self.config=ind_config
        self.ind1=ind_config["base_ind"]
        self.ind2=ind_config.get("ind2",None)
        self.smear_flag=self.config.get("smear",False)
        self.diff_flag = self.config.get("diff", False)
        self.comb_type=self.config.get('comb_type',None)
        self.outlier_value=self.config.get("outlier_value",None)
        print("comb_type=",self.comb_type)

    def update(self,trader):
        print("update in")
        ind1 = trader.indicators[self.ind1].values
        val=ind1[-1]
        if(self.ind2 != None):
            ind2 = trader.indicators[self.ind2].values
        if(self.smear_flag):
            if((len(ind1)>1) & (ind1[-1]==0)):
                val=ind1[-2]
        if(self.diff_flag):
            if(len(ind1)>1):
                val=ind1[-1]-ind1[-2]
        if(self.comb_type=="ema"):
            if(len(ind1)==1):
                val=ind1[-1]
            else:
                val=ind1[-1]+(1/float(self.config['alpha']))*ind1[-2]
        if(self.comb_type=="eq"):
            if(abs(ind1[-1]-ind2[-1])<EPS):
                val=1
            else:
                val=0
        if(self.comb_type=="sum"):
            val=ind1[-1]+ind2[-1]
        if(self.comb_type=="minus"):
            val=ind1[-1]-ind2[-1]
        if(self.comb_type=="prod"):
            val=ind1[-1]*ind2[-1]

        outlier_val = outliers.get(self.name)
        if self.outlier_value != None:
            outlier_val = min(outlier_val,self.outlier_value)
        if(outlier_val!=None):
            if ind_config.get("outlier_reject"):
                vsl = int((abs(val) <= outlier_val)) * val
            elif ind_config.get("outlier_clip"):
                val = np.clip(val,-outlier_val, outlier_val)

        if(ind_config.get("demean")):
            if(means.get(self.name)!=None):
                val=val-means[self.name]

        print(self.name," derived value=",val)
        self.values.append(val)
        print("out")

class Trader:
    def __init__(self, initial_value):
        argJson = json.loads(initial_value)
        self.start_time = datetime.timestamp(datetime.now())
        # save initial values
        self.counter_trades = 0
        self.counter_obs = 0
        self.counter_candles = 0
        self.base_indicators=base_indicators
        self.derived_indicator_configs=derived_indicator_configs
        self.coeffs=coeffs
        self.indicators={}
        self.decays=[]
        for ind_name in self.base_indicators:
            first_index = ind_name.find(":")
            ext_name = ind_name[first_index + 1 :]
            print("ext_name=", ext_name)
            self.indicators[ext_name]=Indicator({"name":ext_name})
            s=ind_name.split(":")
            if("center_of_mass" in s):
                self.decays.append(float(s[-1]))
        self.derived_indicators={}
        for config in self.derived_indicator_configs:
            self.indicators[config['name']]=DerivedIndicator(config)
        self.preds=[]
        self.last_trade_price=0
        self.last_trade_size=0
        self.last_trade_side=""
        self.last_bid_price=0
        self.last_bid_size=0
        self.last_ask_price=0
        self.last_ask_size=0
        self.last_com={}
        self.trade_flag=0
        self.last_pred=0


    def receive_trade(self, trade):
        #time = trade[0]
        #side = trade[1] # 0 = buy, 1 = sell
        #price = trade[2]
        #amount = trade[3] # in btc
        #if self.counterTrades % 1000 == 0:
        #    print("receiveTrade", time, price)
        self.counter_trades += 1
        # spread will be backtested with genetic fitting
        #spread = options["spread"]
        self.last_trade_price=trade.price
        self.last_trade_size=trade.size
        self.last_trade_side=trade.side
        self.tradeFlag=1
        print("trade = ",trade)

    def receive_ob(self, quote):
        self.counter_obs += 1
        self.last_bid_price=quote.bid_price
        self.last_bid_size=quote.bid_size
        self.last_ask_price=quote.ask_price
        self.last_ask_size=quote.ask_size
        self.last_com=quote.com
        return []

    def receive_candle(self, candle, positions, orders, options):
        if self.counter_candles % 100 == 0:
            print('receiveCandle', candle)
        self.counter_candles += 1
        return []

    def post_update(self):
        self.tradeFlag=0

trader_instance = Trader("{}")

def update_indicators():
    global trader_instance
    for ind in trader_instance.indicators.keys():
        print(ind)
        trader_instance.indicators[ind].update(trader_instance)
    trader_instance.post_update()

def gen_pred():
    global trader_instance
    pred=0
    for ind in trader_instance.coeffs.keys():
        pred+=coeffs[ind]*trader_instance.indicators[ind].values[-1]
    trader_instance.last_pred=pred
    print("pred = ", pred)

def init(arg):
    arg_json = json.loads(arg)
    global trader_instance
    print("init", arg_json["options"])

def complete(arg):
    global trader_instance
    arg_json = json.loads(arg)
    end_time = datetime.timestamp(datetime.now())
    time_spend = end_time - trader_instance.start_time
    print("took", time_spend)
    print("completed", arg_json["options"])
    # print your orders execution data
    #print("fills", argJson["fills1"])

def position_change(arg):
    global trader_instance


class MyListener(stomp.ConnectionListener):
    global trader_instance
    def __init__(self, conn):
        self.conn = conn

    def on_error(self, frame):
        print('received an error "%s"' % frame.body)

    def on_message(self, frame):
        data=json.loads(frame.body)
        #print("received a message", data)
        #print("\n")
        if (data['e'] == 'tf'):
            print("tf",data)
            trade_msg=data['d']
            trade=Trade(price=trade_msg['r'], size=trade_msg['a'], side=trade_msg['s'],time=trade_msg['ts'])
            trader_instance.receive_trade(trade)
            update_indicators()
            gen_pred()

        if (data['e'] == 'obstream'):
            quote_msg=data['d']
            bid_vec=quote_msg['b']
            while ((len(bid_vec)>0) and (bid_vec[-1][1]==0)):
                bid_vec=bid_vec[:-1]
            if(len(bid_vec)==0):
                bid_price = 0
                bid_size = 0
            else:
                bid_price = bid_vec[-1][0]
                bid_size = bid_vec[-1][1]


            ask_vec=quote_msg['a']
            while ((len(ask_vec)>0) and (ask_vec[0][1]==0)):
                ask_vec=ask_vec[1:]
            if(len(ask_vec)==0):
                ask_price=0
                ask_size=0
            else:
                ask_price=ask_vec[0][0]
                ask_size=ask_vec[0][1]
            print("new ask vec=",ask_vec)
            com={}
            for d in trader_instance.decays:
                com[d]=0
                if((len(ask_vec)!=0)&(len(bid_vec)!=0)):
                    com[d]=center_of_mass(ask_vec,bid_vec,0.5*(ask_price+bid_price),d)

            quote=Quote(bid_price=bid_price, bid_size=bid_size,ask_price=ask_price, ask_size=ask_size,com=com,time=quote_msg['ts'])
            trader_instance.receive_ob(quote)


    def on_disconnected(self):
        print("disconnected")
        connect_and_subscribe(self.conn)


conn.set_listener("", MyListener(conn))
print("listening")
connect_and_subscribe(conn)

time.sleep(1009999)
