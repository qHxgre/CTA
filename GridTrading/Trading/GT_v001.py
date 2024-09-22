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



class GT_v001(CtaTemplate):
    """单品种期货网格交易"""
    className = 'GT_v001'
    author = 'hxgre'
    name = 'BigHFB'                # 策略实例名称

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
        self.account_id = '30501598'
        self.order_volume = 12       # 下单1手
        self.grid_interval = 4      # 网格间距1个点
        self.base_grid = 0      # 基准网格线
        self.trigger_shift = 0      # 发单偏移量
        self.current_grid, self.next_grid, self.last_grid = 0, 0, 0

        """gridline_records
        记录每个网格线的情况，示例如下：
        gridline_records = {
            grid_line: {
                "order_id" : [委托编号],
                "open_qty": 开仓数量, 
                "close_qty": 平仓数量,
            },
        }"""
        self.gridline_records = {}

        """order_info
        由于无限意无法获取历史委托单的信息，所以我们手动记录相关信息，示例如下：
        order_info = {
            订单号: {
                "direction": "方向",
                "offset": "开平",
                "status": "状态",
                "price": "委托价格",
                "order_volume": "委托数量",
                "traded_volume": "成交数量",
            }
        }
        """
        self.orders_info = {}
        self.jFilePath = 'SRdata.json'

        # 设置策略的参数
        self.onUpdate(setting)

    def onStart(self):
        super().onStart()
        self.initialize_before_trading()


    def onTick(self, tick):
        super().onTick(tick)
        self.putEvent()     # 更新时间，推送状态
        # 非交易时间直接返回
        if not self.time_check(tick.time, 'trading'):
            self.write_log(f'{tick.time} 为非交易时间！')
            return

        if tick.askPrice1 > self.base_grid:         # 做空方向
            book_price = tick.askPrice1     # 盘口价格
            if book_price >= self.next_grid - self.trigger_shift:        # 开仓
                self.send_order(DIRECTION_SHORT, OFFSET_OPEN, self.next_grid, self.order_volume)
            if book_price <= self.last_grid + self.trigger_shift:       # 平仓
                order_volume = self.gridline_records[self.last_grid]["open_qty"] - self.gridline_records[self.last_grid]["close_qty"]
                self.send_order(DIRECTION_SHORT, OFFSET_CLOSE, self.last_grid, order_volume)
        elif tick.bidPrice1 < self.base_grid:       # 做多方向
            book_price = tick.bidPrice1           # 盘口价格
            if book_price <= self.next_grid + self.trigger_shift:       # 开仓
                self.send_order(DIRECTION_LONG, OFFSET_OPEN, self.next_grid, self.order_volume)
            if book_price >= self.last_grid - self.trigger_shift:       # 平仓
                order_volume = self.gridline_records[self.last_grid]["open_qty"] - self.gridline_records[self.last_grid]["close_qty"]
                self.send_order(DIRECTION_SHORT, OFFSET_CLOSE, self.last_grid, order_volume)
        else:
            return      # 当前价格不在可开仓范围内，不交易

    def onOrder(self, order, log=False):
        """委托回报"""
        if order is None:
            return
        self.write_log("\n【委托回报】{} | 时间: {} | 委托编号: {} | 方向: {} | 开平: {} | 状态: {} | 价格: {} | 下单数量: {} | 成交数量: {}".format(
            order.symbol, order.orderTime, order.orderID, order.direction, order.offset, order.status, order.price, order.totalVolume, order.tradedVolume
        ))
        self.orders_info[order.orderID] = {
            "direction": DIRECTION_SHORT if order.direction=='空' else DIRECTION_LONG,
            "offset": OFFSET_OPEN if order.offset=='开仓' else OFFSET_CLOSE,
            "status": order.status, "price": order.price,
            "order_volume": order.totalVolume, "traded_volume": order.tradedVolume,
        }
    
        # 根据委托汇报信息更新记录
        if order.offset == "开仓":
            gridline = self.find_gridline(price=order.price)
            self.update_gridline_records(gridline=gridline, order_id=order.orderID, open_qty=order.tradedVolume, close_qty=None)
            if order.tradedVolume != 0:     # 开仓时，只要当这个网格线有一手成交，就可以以这个开仓价格作为当前网格线更新参数
                self.update_params(DIRECTION_SHORT if order.direction == "卖" else DIRECTION_LONG, order.price)
        elif order.offset in ["平今", "平仓", "平昨"]:
            gridline = self.find_gridline(order_id=order.orderID)
            self.update_gridline_records(gridline=gridline, order_id=order.orderID, open_qty=None, close_qty=order.tradedVolume)
            if gridline not in self.gridline_records:     # 平仓时，只有当这个网格线平完后被删 除了，才能以这个平仓价格作为当前网格线更新参数
                self.update_params(DIRECTION_SHORT if order.direction == "卖" else DIRECTION_LONG, order.price)

        # 撤单时，如果该网格线的开仓数量为0，则直接删除该网格线
        if order.status == "已撤销":
            gridline = self.find_gridline(order_id=order.orderID)
            if self.gridline_records[gridline]["open_qty"] == 0:
                self.write_log(f"【撤销委托】撤单时，若该网格线 {gridline} 的开仓数量为0，则从记录信息中删除: {self.gridline_records[gridline]}")
                del self.gridline_records[gridline]
                self.save_records(self.jFilePath)


    def onTrade(self, trade, log=False):
        """成交回报"""
        self.write_log("\n【成交回报】{} | 时间: {} | 成交编号: {} | 委托编号: {} | 方向: {} | 开平: {} | 价格: {} | 数量: {}".format(
            trade.symbol, trade.tradeTime, trade.tradeID, trade.orderID, trade.direction, trade.offset, trade.price, trade.volume
        ))
        #由于委托回报和成交之间还是有差异，所以我们在成交回报中确认 是否删除
        self.delete_gridline_records(self.find_gridline(order_id=trade.orderID))


    def write_log(self, msg: str, std: int=1):
        """打印日志"""
        if std != 0:
            self.output(msg)

    def print_grids(self, current_grid: int, last_grid: int, next_grid: int) -> str:
        return "上个网格: {}，当前网格: {}, 下个网格: {}".format(last_grid, current_grid, next_grid)

    def initialize_before_trading(self) -> None:
        """盘前初始化"""
        # 读取隔夜JSON文件
        self.jFilePath = 'C:\\Users\\Administrator\\AppData\\Roaming\\InfiniTrader_Simulation\\pyStrategy\\files\\DataGT.json'
        if not os.path.exists(self.jFilePath):
            with open(self.jFilePath, 'w') as jfile:
                json.dump(self.gridline_records, jfile, indent=4)
        else:
            with open(self.jFilePath, 'r') as jfile:
                self.gridline_records = json.load(jfile)
        # 隔夜持仓的订单编号都是 -1
        self.gridline_records = {int(float(key)): {"order_id": [-1], "open_qty": value["open_qty"], "close_qty": value["close_qty"]} for key, value in self.gridline_records.items()}
        self.write_log(f"\n【盘前处理】合约: {self.vtSymbol}\n 读取隔夜数据: \n{self.gridline_records}")

        # 如果没有隔夜数据，则当前网格设置为基础价格，且默认做多
        if not self.gridline_records:
            self.current_grid = self.base_grid
            self.next_grid = self.current_grid - self.grid_interval
            self.last_grid = self.current_grid + self.grid_interval
            self.write_log(f"\n【盘前处理】没有隔夜数据，则设置默认参数：{self.print_grids(self.current_grid, self.last_grid, self.next_grid)}")
        else:
            sorted_gridlines = sorted(self.gridline_records.keys())
            min_grid, max_grid = min(self.gridline_records.keys()), max(self.gridline_records.keys())
            if max_grid < self.base_grid:       # 做多
                self.current_grid = min_grid
                self.next_grid = self.current_grid - self.grid_interval
                self.last_grid = sorted_gridlines[1]
            elif min_grid > self.base_grid:     # 做空
                self.current_grid = max_grid
                self.next_grid = self.current_grid + self.grid_interval
                self.last_grid = sorted_gridlines[-2]
            self.write_log(f"\n【盘前处理】隔夜数据，当前网格参数为：{self.print_grids(self.current_grid, self.last_grid, self.next_grid)}")

    def send_order(self, direction, offset, price, volume):
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

        # 确定网格线
        gridline = self.find_gridline(price=self.next_grid if offset == OFFSET_OPEN else self.current_grid)

        if self.check_before_send(gridline, offset) is False:
            return
        self.cancel_before_send(offset)       # 发单前先撤单
        order_id = self.sendOrder(
            orderType=orderType, price=price, volume=volume, symbol=self.vtSymbol, exchange=self.exchange
        )
        self.write_log("\n【发委托单】网格线: {}, 合约: {}, 委托编号: {}, 买卖: {}, 开平: {}, 价格: {}, 数量: {}".format(
            gridline, self.vtSymbol, order_id, "做空" if direction == DIRECTION_SHORT else "做多",
            "开仓" if offset==OFFSET_OPEN else "平仓", price, volume
        ))

        # 更新记录
        if offset == OFFSET_OPEN:
            self.update_gridline_records(gridline=self.next_grid, order_id=order_id, open_qty=0, close_qty=None)
        elif offset == OFFSET_CLOSE:
            self.update_gridline_records(gridline=self.current_grid, order_id=order_id, open_qty=None, close_qty=0)

    def check_before_send(self, gridline: int, offset: int) -> bool:
        """发委托前检查"""
        if (offset==OFFSET_OPEN) & (gridline in self.gridline_records):
            # 避免重复发开仓单：网格已开仓，则不用开仓
            return False
        if offset==OFFSET_CLOSE:
            # 避免重复发平仓单：遍历gridline的order_id，如果有个平仓挂单则不允许发平仓单
            for order_id in self.gridline_records[gridline]["order_id"]:
                if order_id < 0:        # 如果是隔夜订单，则跳过不检查
                    continue
                if (self.orders_info[order_id]["offset"] == OFFSET_CLOSE) and (self.orders_info[order_id]["status"] in ["未知", "未成交", "部分成交", "部分撤单还在队列"]):
                    self.write_log(f"【拒绝发单】该网格线 {gridline} 已有平仓挂单 {order_id}：{self.gridline_records[gridline]}, 订单信息如下：{self.orders_info[order_id]}")
                    return False
        return True

    def update_gridline_records(self, gridline: int, order_id: int, open_qty: Optional[int], close_qty: Optional[int]) -> None:
        """更新记录，参数如下：
        # gridline: int, 网格线
        # order_id: int, order_id,
        # open_qty: int, 开仓数量
        # close_qty: int, 平仓数量
        """
        if gridline not in self.gridline_records:
            self.gridline_records[gridline] = {"order_id" : [], "open_qty": 0,  "close_qty": 0}
        if order_id not in self.gridline_records[gridline]["order_id"]:
            # 如果该委托单没在列表里面才新增
            self.gridline_records[gridline]["order_id"].append(order_id)

        if open_qty is not None:        # 更新开仓单数据
            self.gridline_records[gridline]["open_qty"] = open_qty
        elif close_qty is not None:     # 更新平仓单数据
            self.gridline_records[gridline]["close_qty"] = close_qty

        self.write_log(f"\n【更新 gridline】{self.gridline_records}")
        self.save_records(self.jFilePath)

    def delete_gridline_records(self, gridline: int):
        """平仓完成后删除相关网格信息，怎么判断平仓完成：
        1. 在 gridline_records 中，open_qty == close_qty
        2. 在 orders_info 中，相关订单处于完成或撤单状态
        """
        condition_1 = self.gridline_records[gridline]["open_qty"] == self.gridline_records[gridline]["close_qty"]
        condition_2 = True
        for order_id in self.gridline_records[gridline]["order_id"]:
            if not self.orders_info[order_id]["status"] in ["全部成交", "已撤销"]:
                condition_2 = False
        
        if condition_1 and condition_2:
            # 平仓完成，则从records中删除该网格线
            self.write_log(f"【平仓完成】删除该开仓网格 {gridline}, 相关信息未: {self.gridline_records[gridline]}")
            del self.gridline_records[gridline]

    def find_gridline(self, price: Optional[int]=None, order_id: Optional[int]=None) -> int:
        """确定网格线
        # 开仓单的网格线：
            # 如果传了 price，则传入价格 price
            # 如果没传 price，则根据 order_id 找对应的网格线
        # 平仓单的网格线
            # 如果有 order_id，则根据 order_id 在 gridline_records 中寻找对应的开仓网格线
            # 如果没有 order_id，则根据传入价格 price 确定
        """
        if (price is None) and (order_id is None):
            raise ValueError(f"定位网格线时需要传入 price - {price} 或者 order_id - {order_id}")
        if price is not None:
            return price
        for gridline, gridinfo in self.gridline_records.items():
            if order_id in gridinfo["order_id"]:
                return gridline
        raise ValueError(f"{order_id} 未找到平仓单信息: {self.gridline_records}")

    def update_params(self, direction: int, price: int) -> None:
        """更新参数，只能当委托有成交才更新
        self.last_grid 是平仓价格的判断标准，如果 self.last_grid 和 self.current_grid 相差了太大间距，可以后期设置哥定时器去补上这些差的网格
        """
        # 更新参数
        self.current_grid = price
        self.next_grid = (price + self.grid_interval) if direction==DIRECTION_SHORT else (price - self.grid_interval)
        sorted_gridlines = sorted(self.gridline_records.keys())
        if len(sorted_gridlines) >= 2:
            self.last_grid = sorted_gridlines[-2] if direction==DIRECTION_SHORT else sorted_gridlines[1]
        else:
            self.last_grid = (self.current_grid - self.grid_interval) if direction==DIRECTION_SHORT else (self.current_grid + self.grid_interval)
        self.write_log(f"\n【更新参数】上个网格: {self.last_grid}，当前网格: {self.current_grid}, 下个网格: {self.next_grid}")

    def cancel_before_send(self, offset: int) -> int:
        """发送委托单前要撤销已挂订单，只撤销相反的单子，即开仓单撤销平仓单，平仓单撤销开场单"""
        for order_id, order_info in self.orders_info.items():
            if offset == OFFSET_OPEN:
                if (order_info["offset"] == OFFSET_CLOSE) and (order_info["status"] in ["未成交", "部分成交"]):
                    self.cancelOrder(order_id)
            elif offset == OFFSET_CLOSE:
                if (order_info["offset"] == OFFSET_OPEN) and (order_info["status"] in ["未成交", "部分成交"]):
                    self.cancelOrder(order_id)
            else:
                raise ValueError(f"不正确的 offset: {offset}")
            self.write_log(f"【撤销委托】撤单id: {order_id}")

    def save_records(self, filepath: str):
        """保存记录信息"""
        records = {}
        for gridline, gridinfo in self.gridline_records.items():
            if gridinfo["open_qty"] == 0:
                continue
            gridinfo["open_qty"] = gridinfo["open_qty"] - gridinfo["close_qty"]
            gridinfo["close_qty"] = 0
            records[gridline] = gridinfo
        with open(filepath, 'w') as jfile:
            json.dump(records, jfile, indent=4)

    def time_check(self, curtime: str, period: str) -> bool:
        """检查当前时间是否在特定的时间段内"""
        self.timer = {
            "trading" : {
                "amfh": ["09:00:00", "10:15:00"],       # 早盘上半场交易时间
                "amsh": ["10:30:00", "11:30:00"],       # 早盘下半场交易时间
                "pm": ["13:30:00", "15:00:00"],         # 下午交易时间
                "night": ["21:00:00", "23:00:00"],      # 夜盘时间
            },
            "auction": ["20:55:00", "20:58:55"],        # 集合竞价时间
            "closing": ["14:59:55", "15:00:00"],        # 尾盘时间
            "launch": ["19:00:00", "20:55:00"],         # 程序启动时间
        }

        try:
            curtime = datetime.strptime(curtime, "%H:%M:%S.%f").strftime("%H:%M:%S")
            temp_time = datetime.strptime(curtime, "%H:%M:%S.%f").time()
            curtime = datetime_time(temp_time.hour, temp_time.minute, temp_time.second)
        except ValueError:
            temp_time = datetime.strptime(curtime, "%H:%M:%S").time()
            curtime = datetime_time(temp_time.hour, temp_time.minute, temp_time.second)


        time_period = self.timer[period]
        # 交易时间检查
        if isinstance(time_period, dict):
            for _, (s, e) in time_period.items():
                start = datetime.strptime(s, "%H:%M:%S").time()
                end = datetime.strptime(e, "%H:%M:%S").time()
                # self.write_log(f"{start} - {curtime} - {end}")
                if start <= curtime <= end:
                    return True
            return False
        # 其他时间检查
        elif isinstance(time_period, list):
            start = datetime.strptime(time_period[0], "%H:%M:%S").time()
            end = datetime.strptime(time_period[1], "%H:%M:%S").time()
            return True if start <= curtime <= end else False
        else:
            raise ValueError("无法识别该时间段:", time_period)
