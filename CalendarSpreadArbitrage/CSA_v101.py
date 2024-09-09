from ctaBase import *
from ctaTemplate import *

import os
import json
import math
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
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
* 价差 = 远月合约 - 近月合约

"""


class CSA_v101(CtaTemplate):
    """单品种跨期套利222"""
    className = 'CSA_v101'
    author = 'hxgre'
    name = 'BigCSA'                # 策略实例名称

    def __init__(self,ctaEngine=None,setting={}):
        """Constructor"""
        super().__init__(ctaEngine,setting)
        # 参数映射表
        self.paramMap = {
            'exchange': '交易所',
            'nearSymbol': '近月合约',
            'farSymbol': '远月合约',
            'account_id': '账户ID',
        }

        # 变量映射表: 可实时监控变量
        self.varMap = {
            'current_spread': '当前价差',
            'max_spread': '已有最大价差',
            'min_spread': '已有最小价差',
            'threshold': '阀值',
        }

        self.exchange = ''
        self.nearSymbol = ''
        self.farSymbol = ''
        self.account_id = ''

        self.price_type = 'opponent'
        self.nearPrice = (0, 0)             # 近期合约的盘口价格
        self.farPrice = (0, 0)          # 远期合约的盘口价格
        self.order_volume = 5                  # 下单数量: 暂时固定为5手
        self.threshold = 2                  # 价差边界 
        self.shortSymbol, self.longSymbol = self.farSymbol, self.nearSymbol



        """记录每个价差的实际开平仓信息，避免重复开平仓
        records = {
            价差: [[委托id], (做空价格, 做空数量), (做多价格, 做多数量)],
            50: [[1,2,3], (6570, 5), (6565, 5)],
        }
        """
        self.records = {}
        self.order_info = {}        # 存储每个订单的信息
        self.current_spread, self.max_spread, self.min_spread = 0, 0, 0

        self.jsonFilePath = 'SRdata.json'

        # 设置策略的参数
        self.onUpdate(setting)

    def onStart(self):
        super().onStart()

        # 读取隔夜JSON文件
        self.jFilePath = 'C:\\Users\\Administrator\\AppData\\Roaming\\InfiniTrader_Simulation\\pyStrategy\\files\\SRdata.json'
        if not os.path.exists(self.jFilePath):
            with open(self.jFilePath, 'w') as jfile:
                json.dump(self.records, jfile, indent=4)
        else:
            with open(self.jFilePath, 'r') as jfile:
                self.records = json.load(jfile)
        self.records = {int(float(key)): value for key, value in self.records.items()}
        self.write_log(f"\n【盘前处理】近期合约: {self.nearSymbol}, 远期合约: {self.farSymbol} \n 读取隔夜数据: \n{self.records}")

        # 订阅合约行情
        self.symbolList = [self.nearSymbol, self.farSymbol]
        self.exchangeList = [self.exchange, self.exchange]
        self.subSymbol()

    def onTick(self, tick):
        super().onTick(tick)
        self.putEvent()     # 更新时间，推送状态

        # 更新近远期合约价格
        if tick.symbol == self.nearSymbol:
            self.nearPrice = (tick.askPrice1, tick.bidPrice1)
        elif tick.symbol == self.farSymbol:
            self.farPrice = (tick.askPrice1, tick.bidPrice1)
        else:
            raise BaseException(f"合约错误！当前合约: {tick.symbol}, 近期合约: {self.nearSymbol}, 远期合约: {self.farSymbol}")

        if (self.farPrice == (0, 0)) or (self.nearPrice == (0, 0)):
            return

        current_spread = self.calc_spread()       # 获取价差
        # 获取已开仓价差的最大值和最小值
        if self.records:
            max_spread, min_spread = max(self.records.keys()), min(self.records.keys())
        else:
            max_spread, min_spread = 0, 0
        self.decide_long_short_contract(current_spread)     # 确定做空/做多的合约
        trade_offset = self.decide_open_or_close(current_spread, max_spread, min_spread)    # 决定是开仓或平仓

        self.current_spread, self.max_spread, self.min_spread = current_spread, max_spread, min_spread

        # 交易逻辑
        if trade_offset == OFFSET_OPEN:
            shortPrice, shortVol, longPrice, longVol = self.calc_open_price_volume()
            actual_spread = math.floor(abs(shortPrice - longPrice))      # 由于前期价差是根据均值计算的，而开仓价差是根据盘口价格计算的，所以记录时要以实际开仓价差为准
            if self.whether_to_trade(offset=trade_offset, spread=actual_spread, max_spread=max_spread, min_spread=min_spread) is False:
                return
            self.write_log("【触发交易】交易方向: {}, 做空合约: {}, 做多合约: {}, 当前价差: {}, 当前最大价差: {}, 当前最小价差: {}".format(
                '开仓' if trade_offset == OFFSET_OPEN else '平仓',
                self.shortSymbol, self.longSymbol, current_spread, max_spread, min_spread
            ))
            self.send_order(DIRECTION_SHORT, OFFSET_OPEN, shortPrice, shortVol, self.shortSymbol, actual_spread)
            self.send_order(DIRECTION_LONG, OFFSET_OPEN, longPrice, longVol, self.longSymbol, actual_spread)
        elif trade_offset == OFFSET_CLOSE:
            shortPrice, shortVol, longPrice, longVol = self.calc_close_price_volume(min_spread)
            actual_spread = math.floor(abs(shortPrice - longPrice))      # 由于前期价差是根据均值计算的，而开仓价差是根据盘口价格计算的，所以记录时要以实际开仓价差为准
            if self.whether_to_trade(offset=trade_offset, spread=actual_spread, max_spread=max_spread, min_spread=min_spread) is False:
                return
            self.write_log("【触发交易】交易方向: {}, 做空合约: {}, 做多合约: {}, 当前价差: {}, 当前最大价差: {}, 当前最小价差: {}".format(
                '开仓' if trade_offset == OFFSET_OPEN else '平仓',
                self.shortSymbol, self.longSymbol, current_spread, max_spread, min_spread
            ))
            self.cancel_open_before_close()     # 发平仓单之前，确认已挂的开仓单全部撤单
            self.send_order(DIRECTION_LONG, OFFSET_CLOSE, shortPrice, shortVol, self.shortSymbol, min_spread)
            self.send_order(DIRECTION_SHORT, OFFSET_CLOSE, longPrice, longVol, self.longSymbol, min_spread)


    def onOrder(self, order, log=False):
        """委托回报"""
        if order is None:
            return
        self.write_log("\n【委托回报】{} | 时间: {} | 委托编号: {} | 方向: {} | 开平: {} | 状态: {} | 价格: {} | 下单数量: {} | 成交数量: {}".format(
            order.symbol, order.orderTime, order.orderID, order.direction, order.offset, order.status, order.price, order.totalVolume, order.tradedVolume
        ))
        self.update_order_info(order.orderID, order.direction, order.offset, order.status)
        spread = self.find_spread(order.orderID)

        if order.offset == '开仓':
            self.update_records(spread, order.orderID, order.symbol, OFFSET_OPEN, order.price, order.tradedVolume)
        elif order.offset == '平今' or order.offset == '平昨' or order.offset == '平仓':
            self.update_records(spread, order.orderID, order.symbol, OFFSET_CLOSE, order.price, order.totalVolume - order.tradedVolume)

    def onTrade(self, trade, log=False):
        """成交回报"""
        self.write_log("\n【成交回报】{} | 时间: {} | 成交编号: {} | 委托编号: {} | 方向: {} | 开平: {} | 价格: {} | 数量: {}".format(
            trade.symbol, trade.tradeTime, trade.orderID, trade.orderID, trade.direction, trade.offset, trade.price, trade.volume
        ))

    def write_log(self, msg: str, std: int=1):
        """打印日志"""
        if std != 0:
            self.output(msg)

    def send_order(self, direction, offset, price, volume, symbol, spread):
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

        # GFD 指令
        order_id = self.sendOrder(
            orderType=orderType, price=price, volume=volume, symbol=symbol, exchange=self.exchange
        )
        self.write_log(f"\n【发委托单】价差: {spread}, 合约: {symbol}, 委托编号: {order_id}, 买卖: {direction}, 开平: {offset}, 价格: {price}, 数量: {volume}")

        # 更新参数
        self.update_records(spread, order_id, symbol, offset, price, 0)

    def find_spread(self, order_id) -> int:
        """根据 order_id 找到对应的 spread"""
        spread = None
        for spread, info in self.records.items():
            if order_id in info[0]:
                return spread
        if spread is None:
            raise f'{order_id} 未找到对应的价差spread: {sorted(self.records.keys())}'

    def update_order_info(self, orderID, direction, offset, status):
        """更新订单信息"""
        if orderID not in self.order_info:
            self.order_info[orderID] = ()
        self.order_info[orderID] = (direction, offset, status)

    def update_records(self, spread, order_id, symbol, offset, price, volume):
        """更新参数
        order_params = [spread, order_id, symbol, offset, price, volume]
        records = {价差: [[委托id], (远期价格, 远期数量), (近期价格, 近期数量)],}
        """
        if offset == OFFSET_OPEN:
            if spread not in self.records:
                self.records[spread] = [[], (0,0), (0,0)]
            if order_id not in self.records[spread][0]:
                self.records[spread][0].append(order_id)
            if symbol == self.shortSymbol:
                self.records[spread][1] = (price, volume)
            elif symbol == self.longSymbol:
                self.records[spread][2] = (price, volume)
            else:
                raise BaseException(f'Unknown transaction: {symbol}')
        elif (offset == OFFSET_CLOSE) & (volume != 0):
            if symbol == self.shortSymbol:
                self.records[spread][1][1] = volume
            elif symbol == self.longSymbol:
                self.records[spread][2][1] = volume
            else:
                raise BaseException(f'Unknown transaction: {symbol}')

            if self.records[spread][1][1] == self.records[spread][2][1] == 0:
                # 平仓完成，则从records中删除该价差
                del self.records[spread]

        self.write_log(f'【更新信息】{self.records}')
        with open(self.jFilePath, 'w') as jfile:
            json.dump(self.records, jfile, indent=4)

    def calc_spread(self) -> float:
        """计算价差，以 askPrice 和 bidPrice 取个平均值计算，且 = 远月 - 近月
        """
        return (sum(self.farPrice) / len(self.farPrice)) - (sum(self.nearPrice) / len(self.nearPrice))

    def decide_long_short_contract(self, spread: float):
        """确定做空和做多的合约
        价差永远是远月减近月，则分为两个情况：
        1. 远月 - 近月 > 0，则说明远月价格更高，当价差变大时，开仓套利对（做空远月/做多近月），待价差变小即均值回归，则平仓套利对（做空远月/做多近月）
        2. 远月 - 近月 < 0，泽说明远月价格更低，当价差变小时（绝对值变大），开仓套利对（做多远月/做空近月），待价差变大（绝对值变小），则平仓套利对（做多远月/做空近月）
        """
        if spread > 0:
            self.shortSymbol, self.shortPrice = self.farSymbol, self.farPrice       # 做空合约是远月合约
            self.longSymbol, self.longPrice = self.nearSymbol, self.nearPrice       # 做多合约是近月合约
        elif spread < 0:
            self.shortSymbol, self.shortPrice = self.nearSymbol, self.nearPrice     # 做空合约是远月合约
            self.longSymbol, self.longPrice = self.farSymbol, self.farPrice         # 做多合约是近月合约
        else:
            raise ValueError("Error! 无法决定做空合约和做多合约。", spread)

    def decide_open_or_close(self, current_spread: int, max_spread: int, min_spread: int) -> Optional[int]:
        """确定开平仓
        """
        if (max_spread == 0) & (min_spread == 0):
            return OFFSET_OPEN      # 第一笔交易总是开仓
        current_spread = abs(current_spread) if current_spread < 0 else current_spread

        if current_spread > max_spread:     # 开仓
            return OFFSET_OPEN 
        elif current_spread < min_spread:       # 平仓
            return OFFSET_CLOSE 
        else:
            return None

    def calc_open_price_volume(self) -> Tuple[float, int]:
        """确定开仓价格和数量
        1. 优先成交: 做空合约以bidPrice1开仓，做多合约以askPrice1下单"""
        return self.shortPrice[1], self.order_volume, self.longPrice[0], self.order_volume

    def calc_close_price_volume(self, spread: float) -> Tuple[float, int]:
        """确定平仓价格和数量
        1. 确保收益：做空合约以bidPrice1买平仓，做多合约以askPrice1卖平仓，这样平仓时的价差会更小，确保收益"""
        shortPrice = self.shortPrice[1]
        shortVol = self.records[spread][1][1]      # 做空合约剩余成交量
        longPrice = self.longPrice[0]
        longVol = self.records[spread][2][1]      # 做多合约剩余成交量
        return shortPrice, shortVol, longPrice, longVol

    def whether_to_trade(self, offset: int, spread: int, max_spread: int, min_spread: int) -> bool:
        """检查当前是否可以交易
        1. 价差变化是否足够
            * 当价差（远月-近月）> 0 时，价差变化 = 当前价差 - 前一价差
            * 当价差（远月-近月）< 0 时，价差变化 = ABS(当前价差 - 前一价差)
            * 无论上述哪种情况，当价差变化增大时，都对应开仓操作，只是做多和做空的合约不同；价差变化变小时，则对应平仓操作。
        2. 开仓则需要保证开仓价差不在已开仓价差中
        """
        if (max_spread == 0) & (min_spread == 0):
            return True      # 第一笔交易不检查
        spread = abs(spread) if spread < 0 else spread

        # 条件1：价差变化足够
        if offset == OFFSET_OPEN:
            spread_change = (spread - max_spread)
        elif offset == OFFSET_CLOSE:
            spread_change = (min_spread - spread)
        else:
            raise ValueError(f"Error! 未识别的offset: {offset}")
        if spread_change < self.threshold:
            return False

        # 条件2：如果是开仓，则当前价差不在已开仓价差中
        if (offset == OFFSET_OPEN) and (spread not in self.records):
            pass
        else:
            return False
        
        # 条件3：如果是平仓，则最小价差要在已开仓价差中
        if (offset == OFFSET_CLOSE) and (min_spread in self.records):
            pass
        else:
            return False        

        # 条件4：开仓价差不能为0
        if spread == 0:
            return False

        return True

    def cancel_open_before_close(self):
        """发平仓单之前，确认已挂的开仓单全部撤单"""
        for orderID, orderInfo in self.order_info.items():
            if orderInfo[1] != '开仓':
                continue
            if orderInfo[2] in ['未成交', '部分成交', '已撤销']:
                self.cancelOrder(orderID)
                self.write_log(f'【撤单】订单编号：{orderID},  订单信息: {orderInfo}')
