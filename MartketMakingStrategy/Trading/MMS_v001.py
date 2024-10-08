from ctaBase import *
from ctaTemplate import *

import os
import json
import math
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from datetime import time as datetime_time
from typing import Optional, Tuple


DIRECTION_SHORT = -1
DIRECTION_LONG = 1
OFFSET_OPEN = 1
OFFSET_CLOSE = 0
STATUS_NOTTRADED = 0
STATUS_PARTTRADED = 1
STATUS_PARTTRADED_PARTCANCELLED = 2
STATUS_ALLTRADED = 3
STATUS_CANCELLED = 4
STATUS_REJECTED = 5
STATUS_UNKNOWN = -1

"""策略思想
在 askPrice1 向上挂5个卖开单，在 bidPrice1 向下挂5个买开单
当 askPrice1 上涨，则成交相应的挂单，并补上相应的挂单，保证5个买开单
当 askPrice1 下跌，则撤去相应的挂单，并重新挂单，保证5个买开单
当 bidPrice1 上涨，则撤去相应的挂单，并重新挂单，保证5个买开单
当 bidPrice1 下跌，则成交相应的挂单，并补上相应的挂单，保证5个买开单

如此，做空和做多的持仓在盘中会不断增加，则需要在尾盘阶段对其进行平仓


是否加入网格策略避免持仓过重的风险？
"""


class MMS_v001(CtaTemplate):
    """做市策略：网格、单品种"""
    className = 'MMS_v001'
    author = 'hxgre'
    name = 'xMMS'                # 策略实例名称

    def __init__(self,ctaEngine=None,setting={}):
        """Constructor"""
        super().__init__(ctaEngine,setting)
        # 参数映射表
        self.paramMap = {
            'exchange': '交易所',
            'vtSymbol': '合约',
            'account_id': '账户ID',
            'base_grid': '基准价格',
            'order_volume': '下单手数',
            'grid_interval': '网格间距',
        }

        # 变量映射表: 可实时监控变量
        self.varMap = {
            'last_grid': '上个网格',
            'current_grid': '当前网格',
            'next_grid': '下个网格',
        }

        self.exchange = ''
        self.vtSymbol = ''
        self.account_id = '18487205898'
        self.order_volume = 5       # 下单手数
        self.interval = 3           # 间隔

        """做市开仓的相关信息
        mm_records = {
            DIRECTION_SHORT: {价格: [订单号, 委托数量, 成交数量]},
            DIRECTION_LONG: {价格: [订单号, 委托数量, 成交数量]},
        }
        """
        self.market_records = {DIRECTION_SHORT: {}, DIRECTION_LONG: {}}
        self.limited_opens = 5          # 最大开仓次数

        """订单信息：由于无限易无法查询每个订单的状态，所以用dict维护订单信息
        order_records = {订单号: [委托价格, 委托数量, 订单状态, 成交价格, 成交数量]}
        """
        self.order_records = {}

        # 设置策略的参数
        self.onUpdate(setting)

    def onStart(self):
        super().onStart()


    def onTick(self, tick):
        super().onTick(tick)
        self.putEvent()     # 更新时间，推送状态

        # 获取做空和做多方向的初始价格
        init_ask, init_bid = min(self.mm_records[DIRECTION_SHORT]), max(self.mm_records[DIRECTION_LONG])
        self.making_market(tick.askPrice1, tick.bidPrice1, init_ask, init_bid)

    def onOrder(self, order, log=False):
        """委托回报"""
        if order is None:
            return
        self.write_log("\n【委托回报】{} | 时间: {} | 委托编号: {} | 方向: {} | 开平: {} | 状态: {} | 价格: {} | 下单数量: {} | 成交数量: {}".format(
            order.symbol, order.orderTime, order.orderID, order.direction, order.offset, order.status, order.price, order.totalVolume, order.tradedVolume
        ))

    def onTrade(self, trade, log=False):
        """成交回报"""
        self.write_log("\n【成交回报】{} | 时间: {} | 成交编号: {} | 委托编号: {} | 方向: {} | 开平: {} | 价格: {} | 数量: {}".format(
            trade.symbol, trade.tradeTime, trade.orderID, trade.orderID, trade.direction, trade.offset, trade.price, trade.volume
        ))

    def write_log(self, msg: str, std: int=1):
        """打印日志"""
        if std != 0:
            self.output(msg)

    def send_order(self, direction, offset, price, volume, symbol: Optional[str]=None, exchange: Optional[str]=None):
        """发单
        FAK (Fill and Kill): FAK 订单要求立即执行所有或部分订单，未能立即执行的部分将被取消。
            例如，如果你下了一个100股的FAK订单，但市场上只有50股可用，那么这个订单会立即购买
            这50股，然后剩下的50股订单将被取消。FAK通常适用于希望快速进入或退出市场，但又不希
            望在没有足够数量股票可供交易时留下未完成的订单的情况。
        FOK (Fill or Kill): FOK 订单要求立即完全执行订单，否则如果不能立即完全执行，整个订单
            就会被取消。例如，如果你下了一个100股的FOK订单，但市场上只有50股可用，那么整个订单
            就会被取消，不会执行任何交易。FOK通常适用于需要大量股票，并且只有当市场上有足够数量
            的股票可供交易时才愿意交易的情况。
        GFD (Good for Day): 是一种交易指令，表示该订单在交易日内有效。如果在当天交易结束时订单
            还未被执行，那么该订单将会被自动取消。这种类型的订单允许投资者在特定的交易日内指定交
            易参数，但不需要一直监视市场。
        """
        if (direction == DIRECTION_SHORT) & (offset == OFFSET_OPEN):
            orderType = CTAORDER_SHORT              # 卖开
        elif (direction == DIRECTION_LONG) & (offset == OFFSET_OPEN):
            orderType = CTAORDER_BUY                # 买开
        elif (direction == DIRECTION_SHORT) & (offset == OFFSET_CLOSE):
            orderType = CTAORDER_SELL               # 卖平
        elif (direction == DIRECTION_LONG) & (offset == OFFSET_CLOSE):
            orderType = CTAORDER_COVER              # 买平

        order_id = self.sendOrder(
            orderType=orderType, price=price, volume=volume, symbol=self.vtSymbol, exchange=self.exchange
        )

    def cancel_order(self, order_id: Optional[list, int]):
        """撤单"""
        if self.order_records[order_id]["status"] in ["未成交", "部分成交"]:
            self.cancel_order(order_id)
            self.write_log(f"【撤销委托】撤单id: {order_id}")

    def making_market(self, ask_price: int, bid_price: int, init_ask: int, init_bid):
        """做市策略核心判断逻辑
        当 askPrice1 上涨，则成交相应的挂单，并补上相应的挂单，保证5个卖开单
        当 askPrice1 下跌，则撤去相应的挂单，并重新挂单，保证5个卖开单
        当 bidPrice1 上涨，则撤去相应的挂单，并重新挂单，保证5个买开单
        当 bidPrice1 下跌，则成交相应的挂单，并补上相应的挂单，保证5个买开单
        """
        if ask_price != init_ask:      # 做空方向有变动
            if ask_price > init_ask:
                # ask_price 上涨，则说明成交了相应的挂单，补上挂单，保证5个卖开单
                if len(self.market_records[DIRECTION_SHORT]) < self.limited_opens:
                    price = ask_price
                    
            elif ask_price < init_ask:
                # ask_price 下跌，则撤去多余的挂单，并重新挂单，保证5个卖开单
                pass

        if bid_price != init_bid:      # 做多方向有变动
            pass


        price = ask_price
        if ask_price > self.initAsk:
            if len(self.mm_records[DIRECTION_SHORT]) < self.open_nums:
                # 如果做空挂单数量不足了，则以最小挂单为基准，向上开始挂单，直至满足最大开仓次数
                for price in range(self.initAsk, ask_price+self.interval*self.open_nums, self.interval):
                    if price not in self.mm_records[DIRECTION_SHORT].keys():
                        self.send_order(direction=DIRECTION_SHORT, offset=OFFSET_OPEN, price=price, volume=self.order_volume)
        elif bid_price < self.initAsk:
            pass
        else:
            pass

        if bid_price > self.initBid:
            pass
        elif bid_price < self.initBid: 
            pass
        else:
            pass    

