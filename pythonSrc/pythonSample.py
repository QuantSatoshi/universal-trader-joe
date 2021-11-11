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
indConfigFile=r"./ind_config.json"
parmFile=r"./parms.json"

Trade = namedtuple("Trade", "price size side time")
Quote = namedtuple("Quote", "bidPrice bidSize askPrice askSize com time")

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


# conn.disconnect()


## to send a prediction
# conn.send('/topic/prediction-1', '0.125', transaction=2)


EPS = 0.000001

def parseConfigFile(config_file: str):
    with open(config_file, "r") as f:
        content = jsmin(f.read().strip())
    parsed_config = json.loads(content)
    return parsed_config


indConfig=parseConfigFile(indConfigFile)
parms=parseConfigFile(parmFile)

baseIndicatorConfigs=indConfig['baseIndicatorConfigs']

derivedIndicatorConfigs=indConfig['derivedIndicatorConfigs']

coeffs=parms['coefs']
means=parms['means']
outliers=parms['outliers']

class Indicator:
    def __init__(self,indConfig):
        self.name=indConfig['name']
        self.values=[]

    def update(self,trader):
        if(self.name=="trade_price"):
            self.values.append(trader.lastTradePrice)
        elif(self.name=="trade_size"):
            self.values.append(trader.lastTradeSize)
        elif(self.name=="trade_side"):
            side=0
            if(trader.lastTradeSide=="b"):
                side=1
            elif(trader.lastTradeSide=="s"):
                side=-1
            self.values.append(side)
        elif (self.name == "bid_price"):
            self.values.append(trader.lastBidPrice)
        elif (self.name == "bid_size"):
            self.values.append(trader.lastBidSize)
        elif(self.name=="ask_price"):
            self.values.append(trader.lastAskPrice)
        elif(self.name=="ask_size"):
            self.values.append(trader.lastAskSize)
        elif(self.name=="mid_price"):
            self.values.append((trader.lastBidPrice+trader.lastAskPrice)*0.5)
        else:
            self.values.append(0)
        print(self.name," ",self.values[-1])

class DerivedIndicator(Indicator):
    def __init__(self,indConfig):
        super().__init__(indConfig)
        self.config=indConfig
        self.ind1=indConfig["base_ind"]
        self.ind2=indConfig.get("ind2",None)
        self.smearFlag=self.config.get("smear",False)
        self.diffFlag = self.config.get("diff", False)
        self.combType=self.config.get('combType',None)
        self.outlierValue=self.config.get('outlierValue',None)
        print("combType=",self.combType)

    def update(self,trader):
        print("update in")
        ind1 = trader.indicators[self.ind1].values
        val=ind1[-1]
        if(self.ind2 != None):
            ind2 = trader.indicators[self.ind2].values
        if(self.smearFlag):
            if((len(ind1)>1) & (ind1[-1]==0)):
                val=ind1[-2]
        if(self.diffFlag):
            if(len(ind1)>1):
                val=ind1[-1]-ind1[-2]
        if(self.combType=="ema"):
            if(len(ind1)==1):
                val=ind1[-1]
            else:
                val=ind1[-1]+(1/float(self.config['alpha']))*ind1[-2]
        if(self.combType=="eq"):
            if(abs(ind1[-1]-ind2[-1])<EPS):
                val=1
            else:
                val=0
        if(self.combType=="sum"):
            val=ind1[-1]+ind2[-1]
        if(self.combType=="minus"):
            val=ind1[-1]-ind2[-1]
        if(self.combType=="prod"):
            val=ind1[-1]*ind2[-1]

        outlier_val = outliers.get(self.name)
        if self.outlierValue != None:
            outlier_val = min(outlier_val,self.outlier_value)
        if(outlier_val!=None):
            if indConfig.get("outlier_reject"):
                vsl = int((abs(val) <= outlier_val)) * val
            elif indConfig.get("outlier_clip"):
                val = np.clip(val,-outlier_val, outlier_val)

        if(indConfig.get("demean")):
            if(means.get(self.name)!=None):
                val=val-means[self.name]

        print(self.name," derived value=",val)
        self.values.append(val)
        print("out")

class Trader:
    def __init__(self, initialValue):
        argJson = json.loads(initialValue)
        self.startTime = datetime.timestamp(datetime.now())
        # save initial values
        self.counterTrades = 0
        self.counterObs = 0
        self.counterCandles = 0
        self.baseIndicatorConfigs=baseIndicatorConfigs
        self.derivedIndicatorConfigs=derivedIndicatorConfigs
        self.coeffs=coeffs
        self.indicators={}
        for config in self.baseIndicatorConfigs:
            self.indicators[config['name']]=Indicator(config)
        self.derivedIndicators={}
        for config in self.derivedIndicatorConfigs:
            self.indicators[config['name']]=DerivedIndicator(config)
        self.preds=[]
        self.lastTradePrice=0
        self.lastTradeSize=0
        self.lastTradeSide=""
        self.lastBidPrice=0
        self.lastBidSize=0
        self.lastAskPrice=0
        self.lastAskSize=0
        self.last_com={}
        self.tradeFlag=0
        self.lastPred=0


    def receiveTrade(self, trade):
        #time = trade[0]
        #side = trade[1] # 0 = buy, 1 = sell
        #price = trade[2]
        #amount = trade[3] # in btc
        #if self.counterTrades % 1000 == 0:
        #    print("receiveTrade", time, price)
        self.counterTrades += 1
        # spread will be backtested with genetic fitting
        #spread = options["spread"]
        self.lastTradePrice=trade.price
        self.lastTradeSize=trade.size
        self.lastTradeSide=trade.side
        self.tradeFlag=1
        print("trade = ",trade)
        #if self.position is None:
        # all instructions can be viewed at https://github.com/z-hao-wang/universal-trader/blob/master/src/instructions.type.ts
        #    return [{
        #        "op": "cancelAllOrders"
        #    }, {
        ##        "op": "createLimitOrder",
         #       "side": "buy",
         #       "price": price - spread,
         #       "amountCurrency": 500,
         #       "ts": time,
         #   }, {
         ##       "op": "createLimitOrder",
          #      "side": "sell",
          #      "price": price + spread,
          #      "amountCurrency": 500,
          #      "ts": time,
          #  }]
        ##else:
         ##   side = "buy" if position.get("side") == "sell" else "sell"
          #  newPrice = price - spread if side == "buy" else price + spread
          #  return [{
          #      "op": "cancelAllOrders"
          #  }, {
          #      "op": "createLimitOrder",
          #      "side": side,
          ##      "price": newPrice,
           #     "amountCurrency": position.get("amountCurrency"),
           #     "ts": time,
           # }]
    #interface OrderBookSchema {
    #   ts: Date; // server timestamp
    #   exchange?: string;
    #   pair?: string;
    #   bids: {r: number, a: number}[];
    #   asks: {r: number, a: number}[];
    # }

    # position { amountOriginal?: number;
    #               amountClosed?: number;
    #               amountCurrency: number; // amount remaining
    #               side: 'buy' | 'sell';
    #               price: number;
    #               pairDb: string;
    #               }
    def receiveOb(self, quote):
        self.counterObs += 1
        self.lastBidPrice=quote.bidPrice
        self.lastBidSize=quote.bidSize
        self.lastAskPrice=quote.askPrice
        self.lastAskSize=quote.askSize
        self.lastCom=quote.com
        return []

    def receiveCandle(self, candle, positions, orders, options):
        if self.counterCandles % 100 == 0:
            print('receiveCandle', candle)
        self.counterCandles += 1
        return []

    def postUpdate(self):
        self.tradeFlag=0

traderInstance = Trader("{}")

def updateIndicators():
    global traderInstance
    for ind in traderInstance.indicators.keys():
        traderInstance.indicators[ind].update(traderInstance)
    traderInstance.postUpdate()

def genPred():
    global traderInstance
    pred=0
    for ind in traderInstance.coeffs.keys():
        pred+=coeffs[ind]*traderInstance.indicators[ind].values[-1]
    traderInstance.lastPred=pred
    print("pred = ", pred)

#def receiveOb(arg):
#    argJson = json.loads(arg)
#    global traderInstance
#    ret = traderInstance.receiveOb(argJson["ob"], argJson.get("position"), argJson["orders"], argJson["options"])
#    return json.dumps(ret)

#def receiveTrade(arg):
#    updateIndicators(arg)
#    argJson = json.loads(arg)
#    global traderInstance
#    ret = traderInstance.receiveTrade(argJson["trade"], argJson.get("position"), argJson["orders"], argJson["options"])
#    return json.dumps(ret)

#def receiveCandle(arg):
#    argJson = json.loads(arg)
#    global traderInstance
#    ret = traderInstance.receiveCandle(argJson["candle"], argJson.get("positions"), argJson["orders"], argJson["options"])
#    return json.dumps(ret)

def init(arg):
    argJson = json.loads(arg)
    global traderInstance
    print("init", argJson["options"])

def complete(arg):
    global traderInstance
    argJson = json.loads(arg)
    endTime = datetime.timestamp(datetime.now())
    timeSpend = endTime - traderInstance.startTime
    print("took", timeSpend)
    print("completed", argJson["options"])
    # print your orders execution data
    #print("fills", argJson["fills1"])

def positionChange(arg):
    global traderInstance


class MyListener(stomp.ConnectionListener):
    global traderInstance
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
            tradeMsg=data['d']
            trade=Trade(price=tradeMsg['r'], size=tradeMsg['a'], side=tradeMsg['s'],time=tradeMsg['ts'])
            traderInstance.receiveTrade(trade)
            updateIndicators()
            genPred()

        if (data['e'] == 'obstream'):
            quoteMsg=data['d']
            bid_vec=quoteMsg['b']
            while ((len(bid_vec)>0) and (bid_vec[-1][1]==0)):
                bid_vec=bid_vec[:-1]
            if(len(bid_vec)==0):
                bidPrice = 0
                bidSize = 0
            else:
                bidPrice = bid_vec[-1][0]
                bidSize = bid_vec[-1][1]


            ask_vec=quoteMsg['a']
            while ((len(ask_vec)>0) and (ask_vec[0][1]==0)):
                ask_vec=ask_vec[1:]
            if(len(ask_vec)==0):
                askPrice=0
                askSize=0
            else:
                askPrice=ask_vec[0][0]
                askSize=ask_vec[0][1]
            print("new ask vec=",ask_vec)
            com={}
            for d in indConfig.get("decays"):
                com[d]=0
                if((len(ask_vec)!=0)&(len(bid_vec)!=0)):
                    com[d]=center_of_mass(ask_vec,bid_vec,0.5*(askPrice+bidPrice),d)

            quote=Quote(bidPrice=bidPrice, bidSize=bidSize,askPrice=askPrice, askSize=askSize,com=com,time=quoteMsg['ts'])
            traderInstance.receiveOb(quote)


    def on_disconnected(self):
        print("disconnected")
        connect_and_subscribe(self.conn)


conn.set_listener("", MyListener(conn))
print("listening")
connect_and_subscribe(conn)

time.sleep(1009999)
