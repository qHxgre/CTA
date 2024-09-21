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



class HFB_v001(CtaTemplate):
    """高频刷单策略：网格、单品种"""
    className = 'HFB_v001'
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
        self.order_volume = 1       # 下单1手
        self.grid_interval = 1      # 网格间距1个点
        self.base_grid = 0      # 基准网格线
        self.trigger_shift = 0      # 发单偏移量
        self.current_grid, self.next_grid, self.last_grid = 0, 0, 0

        """gridline_records 记录每个网格线的情况，示例如下：
        gridline_records = {
            grid_line: {
                "order_id" : [委托编号],
                "open_qty": 开仓数量, 
                "close_qty": 平仓数量,
            },
        }"""
        self.gridline_records = {}
        """orders_status 记录每个委托单的委托状态，用于后续撤单，示例如下：
        order_statuses = {订单号: (订单方向, 订单开平, 订单状态) ...}"""
        self.orders_status = {}
        self.jFilePath = 'SRdata.json'

        # 设置策略的参数
        self.onUpdate(setting)

    def onStart(self):
        super().onStart()

        # 读取隔夜JSON文件
        self.jFilePath = 'C:\\Users\\Administrator\\AppData\\Roaming\\InfiniTrader_Simulation\\pyStrategy\\files\\HFBdata.json'
        if not os.path.exists(self.jFilePath):
            with open(self.jFilePath, 'w') as jfile:
                json.dump(self.gridline_records, jfile, indent=4)
        else:
            with open(self.jFilePath, 'r') as jfile:
                self.gridline_records = json.load(jfile)
        self.gridline_records = {int(float(key)): {"order_id": [-1], "open_qty": value["open_qty"], "close_qty": value["close_qty"]} for key, value in self.gridline_records.items()}
        self.write_log(f"\n【盘前处理】合约: {self.vtSymbol}\n 读取隔夜数据: \n{self.gridline_records}")

        # 如果没有隔夜数据，则当前网格设置为基础价格，且默认做多
        if not self.gridline_records:
            self.current_grid = self.base_grid
            self.next_grid = self.current_grid - self.grid_interval
            self.last_grid = self.current_grid + self.grid_interval
            self.write_log(f"\n【盘前处理】没有隔夜数据，则设置默认参数！上个网格: {self.last_grid}，当前网格: {self.current_grid}, 下个网格: {self.next_grid}")
        else:
            sorted_gridlines = sorted(self.gridline_records.keys())
            min_grid, max_grid = min(self.gridline_records.keys()), max(self.gridline_records.keys())
            if max_grid < self.base_grid:
                self.current_grid = min_grid
                self.next_grid = self.current_grid - self.grid_interval
                self.last_grid = sorted_gridlines[1]
            elif min_grid > self.base_grid:
                self.current_grid = max_grid
                self.next_grid = self.current_grid + self.grid_interval
                self.last_grid = sorted_gridlines[-2]


    def onTick(self, tick):
        super().onTick(tick)
        self.putEvent()     # 更新时间，推送状态
        # 非交易时间直接返回
        if not self.time_check(tick.time, 'trade_time'):
            self.write_log(f'{tick.time} is not in trading time')
            return

        if tick.askPrice1 > self.base_grid:         # 做空方向
            book_price = tick.askPrice1     # 盘口价格
            if book_price >= self.next_grid - self.trigger_shift:        # 开仓
                self.send_order(DIRECTION_SHORT, OFFSET_OPEN, self.next_grid, self.order_volume)
            if book_price <= self.last_grid + self.trigger_shift:       # 平仓
                order_volume = self.gridline_records[self.last_grid]["open_qty"] - self.gridline_records[self.last_grid]["close_qty"]
                self.send_order(DIRECTION_SHORT, OFFSET_CLOSE, self.last_grid, order_volume)
        elif tick.bidPrice1 < self.base_grid:       # 做多防线
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
        self.update_status(order.orderID, DIRECTION_SHORT if order.direction=='空' else DIRECTION_LONG, OFFSET_OPEN if order.offset=='开仓' else OFFSET_CLOSE, order.status)

        # 根据委托汇报信息更新记录
        if order.offset == "开仓":
            gridline = self.find_gridline(price=order.price)
            self.update_records(gridline=gridline, order_id=order.orderID, open_qty=order.tradedVolume, close_qty=None)
            if order.tradedVolume != 0:     # 开仓时，只要当这个网格线有一手成交，就可以以这个开仓价格作为当前网格线更新参数
                self.update_params(DIRECTION_SHORT if order.direction == "卖" else DIRECTION_LONG, order.price)
        elif order.offset in ["平今", "平仓", "平昨"]:
            gridline = self.find_gridline(order_id=order.orderID)
            self.update_records(gridline=gridline, order_id=order.orderID, open_qty=None, close_qty=order.tradedVolume)
            if gridline not in self.gridline_records:     # 平仓时，只有当这个网格线平完后被删除了，才能以这个平仓价格作为当前网格线更新参数
                self.update_params(DIRECTION_SHORT if order.direction == "卖" else DIRECTION_LONG, order.price)

        # 撤单时，如果该网格线的开仓数量为0，则直接删除该网格线
        if order.status == "已撤销":
            gridline = self.find_gridline(order_id=order.orderID)
            if self.gridline_records[gridline]["open_qty"] == 0:
                self.write_log(f"【撤销委托】撤单时，若该网格线 {gridline} 的开仓数量为0，则从记录信息中删除: {self.gridline_records[gridline]}")
                del self.gridline_records[gridline]
                self.save_records()


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

        # 确定网格线
        if offset == OFFSET_OPEN:
            gridline = self.find_gridline(price=self.next_grid)
        elif offset == OFFSET_CLOSE:
            gridline = self.find_gridline(price=self.current_grid)

            self.write_log("\n【预发委托】网格线: {}, 合约: {}, 买卖: {}, 开平: {}, 价格: {}, 数量: {}".format(
                gridline, self.vtSymbol, "做空" if direction == DIRECTION_SHORT else "做多",
                "开仓" if offset==OFFSET_OPEN else "平仓", price, volume
            ))

        if self.check_before_send(gridline, offset) is False:
            return
        self.cancel_before_send()       # 发单前先撤单
        order_id = self.sendOrder(
            orderType=orderType, price=price, volume=volume, symbol=self.vtSymbol, exchange=self.exchange
        )
        self.write_log("\n【发委托单】网格线: {}, 合约: {}, 委托编号: {}, 买卖: {}, 开平: {}, 价格: {}, 数量: {}".format(
            gridline, self.vtSymbol, order_id, "做空" if direction == DIRECTION_SHORT else "做多",
            "开仓" if offset==OFFSET_OPEN else "平仓", price, volume
        ))

        # 更新记录
        if offset == OFFSET_OPEN:
            self.update_records(gridline=self.next_grid, order_id=order_id, open_qty=0, close_qty=None)
        elif offset == OFFSET_CLOSE:
            self.update_records(gridline=self.current_grid, order_id=order_id, open_qty=None, close_qty=0)

        # 更新订单状态
        self.update_status(order_id, direction, offset, "未知")

    def check_before_send(self, gridline: int, offset: int) -> bool:
        """发委托前检查"""

        if (offset==OFFSET_OPEN) & (gridline in self.gridline_records):
            # 避免重复发开仓单
            # self.write_log(f"【拒绝发单】该网格线 {gridline} 已开仓：{self.gridline_records[gridline]}")
            return False
        if offset==OFFSET_CLOSE:
            # 避免重复发平仓单：遍历gridline的order_id，如果有个平仓挂单则不允许发平仓单
            for order_id in self.gridline_records[gridline]["order_id"]:
                if order_id < 0:        # 如果是隔夜订单，则跳过不检查
                    continue
                self.write_log(f"test {order_id}: {self.orders_status[order_id]}")
                if (self.orders_status[order_id][1] == OFFSET_CLOSE) and (self.orders_status[order_id][2] in ["未知", "未成交", "部分成交", "部分撤单还在队列"]):
                    self.write_log(f"【拒绝发单】该网格线 {gridline} 已有平仓挂单 {order_id}：{self.gridline_records[gridline]}, {self.orders_status[order_id]}")
                    return False
        return True

    def update_records(self, gridline: int, order_id: int, open_qty: Optional[int], close_qty: Optional[int]) -> None:
        """更新记录
        # 参数
        # gridline: int, 网格线
        # order_id: int, order_id,
        # open_qty: int, 开仓数量
        # close_qty: int, 平仓数量
        """
        if gridline not in self.gridline_records:
            self.gridline_records[gridline] = {
                "order_id" : [],
                "open_qty": 0, 
                "close_qty": 0,
            }
        if order_id not in self.gridline_records[gridline]["order_id"]:
            # 如果该委托单没在列表里面才新增
            self.gridline_records[gridline]["order_id"].append(order_id)

        if open_qty is not None:
            self.gridline_records[gridline]["open_qty"] = open_qty
        if close_qty is not None:
            self.gridline_records[gridline]["close_qty"] = close_qty
            if self.gridline_records[gridline]["open_qty"] == self.gridline_records[gridline]["close_qty"]:
                # 平仓完成，则从records中删除该网格线
                self.write_log(f"【平仓完成】删除该开仓网格 {gridline}, 相关信息未: {self.gridline_records[gridline]}")
                del self.gridline_records[gridline]

        self.write_log(f"\n【更新信息】{self.gridline_records}")
        self.save_records()

    def find_gridline(self, price: Optional[int]=None, order_id: Optional[int]=None) -> int:
        """确定网格线
        # 开仓单的网格线：
            # 如果传了 price，则传入价格 price
            # 如果没传 price，则根据 order_id 找对应的网格线
        # 平仓单的网格线
            # 如果有 order_id，则根据 order_id 在 gridline_records 中寻找对应的开仓网格线
            # 如果没有 order_id，则根据传入价格 price 确定
        """
        if price is not None:
            return price
        if order_id is None:
            raise ValueError(f"定位网格线时需要传入 price - {price} 或者 order_id - {order_id}")
        for gridline, gridinfo in self.gridline_records.items():
            if order_id in gridinfo["order_id"]:
                return gridline
        raise ValueError(f"{order_id} 未找到平仓单信息: {self.gridline_records}")

    def update_status(self, order_id: int, direction: int, offset: int, status: str) -> None:
        self.orders_status[order_id] = (direction, offset, status)

    def update_params(self, direction: int, price: int) -> None:
        """更新参数，只能当委托有成交才更新
        self.last_grid 是平仓价格的判断标准，如果 self.last_grid 和 self.current_grid 相差了太大间距，可以后期设置哥定时器去补上这些差的网格"""
        # 更新参数
        self.current_grid = price
        self.next_grid = (price + self.grid_interval) if direction==DIRECTION_SHORT else (price - self.grid_interval)
        sorted_gridlines = sorted(self.gridline_records.keys())
        if len(sorted_gridlines) >= 2:
            self.last_grid = sorted_gridlines[-2] if direction==DIRECTION_SHORT else sorted_gridlines[1]
        else:
            self.last_grid = (self.current_grid - self.grid_interval) if direction==DIRECTION_SHORT else (self.current_grid + self.grid_interval)
        self.write_log(f"\n【更新参数】上个网格: {self.last_grid}，当前网格: {self.current_grid}, 下个网格: {self.next_grid}")

    def cancel_before_send(self) -> int:
        """发送委托单前都要撤销已挂订单，只撤销相反的单子，即开仓单撤销平仓单，平仓单撤销开场单"""
        for order_id, order_info in self.orders_status.items():
            status = order_info[2]
            if status in ["未成交", "部分成交"]:
                self.cancelOrder(order_id)
                self.write_log(f"【撤销委托】撤单id: {order_id}")

    def save_records(self):
        """保存记录信息"""
        records = {}
        for gridline, gridinfo in self.gridline_records.items():
            if gridinfo["open_qty"] == 0:
                continue
            gridinfo["open_qty"] = gridinfo["open_qty"] - gridinfo["close_qty"]
            gridinfo["close_qty"] = 0
            records[gridline] = gridinfo
        with open(self.jFilePath, 'w') as jfile:
            json.dump(records, jfile, indent=4)


    def time_check(self, curtime: str, flag: str) -> bool:
        """检查当前时间是否在特定的时间段内"""
        self.am_h1_start = '09:00:00'           # 早盘1开始时间
        self.am_h1_end = '10:15:00'             # 早盘1结束时间
        self.am_h2_start = '10:30:00'           # 早盘2开始时间
        self.am_h2_end = '11:30:00'             # 早盘2结束时间
        self.pm_start = '13:30:00'              # 下午开始时间
        self.pm_end = '15:00:00'                # 下午结束时间
        self.night_start = '21:00:00'           # 夜盘开始时间
        self.night_end = '23:00:00'             # 夜盘结束时间
        self.start_start = '19:00:00'           # 程序启动开始时间
        self.start_end = '20:59:00'             # 程序启动结束时间
        self.auction_start = '20:55:00'         # 集合竞价开始时间
        self.auction_end = '20:58:55'           # 集合竞价结束时间
        self.end_start = '14:59:55'             # 尾盘开始时间
        self.end_end = '15:00:00'               # 尾盘结束时间

        def get_cancel_time(start_time: str):
            """获取撤单时间"""
            start_time = datetime.strptime(start_time, '%H:%M:%S')
            cancel_start = start_time + timedelta(minutes=5)
            cancel_end = start_time + timedelta(minutes=6)
            return cancel_start.strftime("%H:%M:%S"), cancel_end.strftime("%H:%M:%S")

        try:
            curtime = datetime.strptime(curtime, "%Y%m%d %H:%M:%S").strftime("%H:%M:%S")
        except ValueError:
            curtime = curtime
        try:
            temp_time = datetime.strptime(curtime, "%H:%M:%S.%f").time()
        except ValueError:
            temp_time = datetime.strptime(curtime, "%H:%M:%S").time()
        curtime = datetime_time(temp_time.hour, temp_time.minute, temp_time.second)
        if flag == "trade_time":
            time_periods = {
                'am_h1': (self.am_h1_start, self.am_h1_end),
                'am_h2': (self.am_h2_start, self.am_h2_end),
                'pm': (self.pm_start, self.pm_end),
                'night': (self.night_start, self.night_end)
            }
            for _, (start, end) in time_periods.items():
                start_time = datetime.strptime(start, "%H:%M:%S").time()
                end_time = datetime.strptime(end, "%H:%M:%S").time()
                if start_time <= curtime <= end_time:
                    return True
            return False
        if flag == "cancel_time":
            # 获取所有的交易时间段
            time_periods = [self.am_h1_start, self.pm_start, self.night_start]
            # 检查每一个交易时间段
            for i in time_periods:
                cancel_start, cancel_end = get_cancel_time(i)
                cancel_start = datetime.strptime(cancel_start, "%H:%M:%S").time()
                cancel_end = datetime.strptime(cancel_end, "%H:%M:%S").time()
                if cancel_start <= curtime <= cancel_end:
                    return True
            return False
        if flag == "start":
            process_start = datetime.strptime(self.start_start, "%H:%M:%S").time()
            process_end = datetime.strptime(self.start_end, "%H:%M:%S").time()
            if process_start <= curtime <= process_end:
                return True
            return False
        if flag == "auction":
            auction_start = datetime.strptime(self.auction_start, "%H:%M:%S").time()
            auction_end = datetime.strptime(self.auction_end, "%H:%M:%S").time()
            if auction_start <= curtime <= auction_end:
                return True
            return False
        if flag == "end":
            end_start = datetime.strptime(self.end_start, "%H:%M:%S").time()
            end_end = datetime.strptime(self.end_end, "%H:%M:%S").time()
            if end_start <= curtime <= end_end:
                return True
            return False
