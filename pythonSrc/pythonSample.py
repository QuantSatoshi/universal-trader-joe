import json
from datetime import datetime

import time
from collections import namedtuple
import stomp

conn = stomp.Connection([("node2.hawkguide.com", 64613)])

Trade = namedtuple("Trade", "price size time")
Quote = namedtuple("Quote", "bidPrice bidSize askPrice askSize time")

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

baseIndicatorConfigs=[
    {"Name":"TradePrice"}, \
    {"Name":"TradeSize"},\
    {"Name":"AskPrice"},\
    {"Name":"AskSize"},\
    {"Name":"BidPrice"},\
    {"Name":"BidSize"},\
    {"Name":"MidPrice"},\
    {"Name":"TradeFlag"},\

]

derivedIndicatorConfigs=[
                         {"Name": "dPrice", "Ind1": "TradePrice", "CombType":"Diff"},\
                         {"Name": "dPrice_60", "Ind1": "dPrice", "CombType":"Ema", "Alpha": 60},\
                         {"Name": "dPrice_20", "Ind1": "dPrice", "CombType":"Ema","Alpha": 20},\
                         {"Name": "dPrice_180", "Ind1": "dPrice", "CombType":"Ema","Alpha": 180},\
                         {"Name": "dMid", "Ind1": "MidPrice", "CombType":"Diff", "outlier_clip": 0.5},\
                         {"Name": "PosSide", "Ind1": "TradePrice","CombType":"Eq","Ind2": "AskPrice"},\
                         {"Name": "NegSide", "Ind1": "TradePrice","CombType":"Eq","Ind2": "BidPrice"},\
                         {"Name": "Side", "Ind1": "PosSide", "CombType":"Sub","Ind2": "NegSide"},\
                         {"Name": "Size", "Ind1": "Side", "CombType":"Prod","Ind2": "TradeSize"},\
                         {"Name": "Side_5", "Ind1": "Side", "CombType":"Ema", "Alpha": 5},\
                         {"Name": "Side_100", "Ind1": "Side", "CombType":"Ema", "Alpha": 100}\
                         ]

coeffs={"Side_5":0.01,"dMid":0.03}

class Indicator:
    def __init__(self,indConfig):
        self.name=indConfig['Name']
        self.values=[]

    def update(self,trader):
        if(self.name=="TradePrice"):
            self.values.append(trader.lastTradePrice)
        elif(self.name=="TradeSize"):
            self.values.append(trader.lastTradeSize)
        elif (self.name == "BidPrice"):
            self.values.append(trader.lastBidPrice)
        elif (self.name == "BidSize"):
            self.values.append(trader.lastBidSize)
        elif(self.name=="AskPrice"):
            self.values.append(trader.lastAskPrice)
        elif(self.name=="AskSize"):
            self.values.append(trader.lastAskSize)
        elif(self.name=="MidPrice"):
            self.values.append((trader.lastBidPrice+trader.lastAskPrice)*0.5)
        else:
            self.values.append(0)
        print(self.name," ",self.values[-1])

class DerivedIndicator(Indicator):
    def __init__(self,indConfig):
        super().__init__(indConfig)
        self.config=indConfig
        self.ind1=indConfig["Ind1"]
        self.ind2=indConfig.get("Ind2",None)
        self.combType=self.config['CombType']
        print("combType=",self.combType)

    def update(self,trader):
        ind1 = trader.indicators[self.ind1].values
        if(self.ind2 != None):
            ind2 = trader.indicators[self.ind2].values
        if (self.combType == "Diff"):
            if (len(ind1) == 1):
                val=0
            else:
                val=ind1[-1]-ind1[-2]
        if(self.combType=="Ema"):
            if(len(ind1)==1):
                val=ind1[-1]
            else:
                val=ind1[-1]+(1/float(self.config['Alpha']))*ind1[-2]
        if(self.combType=="Eq"):
            if(abs(ind1[-1]-ind2[-1])<EPS):
                val=1
            else:
                val=0
        if(self.combType=="Sum"):
            val=ind1[-1]+ind2[-1]
        if(self.combType=="Sub"):
            val=ind1[-1]-ind2[-1]
        if(self.combType=="Prod"):
            val=ind1[-1]*ind2[-1]
        print(self.name," derived value=",val)
        self.values.append(val)

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
            self.indicators[config['Name']]=Indicator(config)
        self.derivedIndicators={}
        for config in self.derivedIndicatorConfigs:
            self.indicators[config['Name']]=DerivedIndicator(config)
        self.preds=[]
        self.lastTradePrice=0
        self.lastTradeSize=0
        self.lastBidPrice=0
        self.lastBidSize=0
        self.lastAskPrice=0
        self.lastAskSize=0
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
        self.tradeFlag=1
        print("trade price = ",trade.price)
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
        print("receive ob")
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
    print("updating")
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
            #print(data)
            tradeMsg=data['d']
            trade=Trade(price=tradeMsg['r'], size=tradeMsg['a'], time=tradeMsg['ts'])
            traderInstance.receiveTrade(trade)
            updateIndicators()
            genPred()

        if (data['e'] == 'obstream'):
            quoteMsg=data['d']
            bidVec=quoteMsg['b']
            while ((len(bidVec)>0) and (bidVec[-1][1]==0)):
                bidVec=bidVec[:-1]
            if(len(bidVec)==0):
                bidPrice = 0
                bidSize = 0
            else:
                bidPrice = bidVec[-1][0]
                bidSize = bidVec[-1][1]

            askVec=quoteMsg['a']
            while ((len(askVec)>0) and (askVec[0][1]==0)):
                askVec=askVec[1:]
            if(len(askVec)==0):
                askPrice=0
                askSize=0
            else:
                askPrice=askVec[0][0]
                askSize=askVec[0][1]

            quote=Quote(bidPrice=bidPrice, bidSize=bidSize,askPrice=askPrice, askSize=askSize,time=quoteMsg['ts'])
            traderInstance.receiveOb(quote)


    def on_disconnected(self):
        print("disconnected")
        connect_and_subscribe(self.conn)


conn.set_listener("", MyListener(conn))
connect_and_subscribe(conn)

time.sleep(1009999)
