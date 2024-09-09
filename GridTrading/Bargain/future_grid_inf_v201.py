"""
Python 中的命名方式，我们一般使用小写下划线命名法，但是有一些特殊情况，需要使用驼峰命名法，以下列出对应的命名规则：

类型	               命名法           示例
包	                  小写下划线        package_name
模块	              小写下划线        module_name
类	                  大驼峰            ClassName
异常	              大驼峰            ExceptionName
函数                  小写下划线        function_name
全局常量/类常量	        大写下划线        GLOBAL_CONSTANT_NAME
全局变量/类变量         小写下划线          global_var_name
实例变量               小写下划线           instance_var_name
方法名                 小写下划线           method_name
函数参数/方法参数       小写下划线	        function_parameter_name
局部变量               小写下划线       local_var_name
"""

from ctaBase import *
from ctaTemplate import *

import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from datetime import time as datetime_time
from typing import Union, Tuple, List, Optional, Dict


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

CLOSE_ADVANCED = 0
CLOSE_WAIT = 1

"""
Logs 2024-08-19
1. 隔夜信息用 json 存储
2. 优化逻辑：
    (1) 往上平仓保持40%
    (2) 网格间距 3-4 个点，跳过整数点
"""

class GridTradeV201(CtaTemplate):
    """期货网格策略"""
    vtSymbol = ''
    exchange = ''
    className = 'GridTradeV201'
    author = 'hxgre'
    name = 'BigFuture'                # 策略实例名称


    def __init__(self,ctaEngine=None,setting={}):
        """Constructor"""
        super().__init__(ctaEngine,setting)
        # 参数映射表
        self.paramMap = {
            'exchange': '交易所',
            'vtSymbol': '合约',
            'account_id': '账户ID',
            'position_filename': '隔夜持仓',
            'strategy_filename': '策略参数',
        }

        # 变量映射表
        self.varMap = {
            'trading' : '交易中',
            'pos': '仓位',
            'curr_open': '当前网格',
            'next_open': '开仓点',
            'next_close': '平仓点',
        }

        # 输入参数
        self.exchange = ''
        self.vtSymbol = ''
        self.account_id = '30501598'

        # 策略参数
        self.strategy_params = {
            'short_max_price': 0,           # 做空最大网格线
            'short_min_price': 0,           # 做空最小网格线
            'long_max_price': 0,            # 做多最大网格线
            'long_min_price': 0,            # 做多最小网格线
            'order_qty': 0,                 # 下单数量
            'cancel_parameter': 0,          # 撤单参数
            'grid_interval': 0,             # 网格间距
            'trigger_shift': 0,             # 下单触发偏移量
            'close_short': 0,               # 平空参数
            'close_long': 0,                # 平多参数
            'base_volume': 0,               # 底仓数量
            'sbase_threshold': 0,           # 做空底仓门槛
            'lbase_threshold': 0,           # 做多底仓门槛
            'close_method': CLOSE_WAIT,     # 平仓方式: WAIT - 指等到平仓点再发单平仓； advanced - 指开仓成交则提前挂平仓单
        } 

        # 策略变量
        self.grid_direction = DIRECTION_LONG        # 网格所在方向
        self.current_grid = 0       # 当前开仓网格
        self.next_grid = 0          # 下一个待开仓网格
        self.last_grid = 0          # 上一个已开仓网格，即待平仓网格
        self.gridlines = {DIRECTION_SHORT: {}, DIRECTION_LONG: {}}      # 记录已开仓网格信息
        self.gridbase = {DIRECTION_SHORT: {}, DIRECTION_LONG: {}}      # 记录底仓网格信息

        # 各类子程序结束的标识
        self.tag_of_execute_process_auction = False
        self.tag_of_execute_process_closeOvernight = False
        self.tag_of_execute_process_cancel = False
        self.tag_of_execute_process = False

        self.timer = timer()

        # 设置策略的参数
        self.onUpdate(setting)

    def onStart(self):
        """盘前处理
        #. 读取当日策略参数
        #. 读取隔夜网格数据
        #. 读取底仓网格数据
        #. 初始化相关存储器
        #. 对网格的补充操作
        #. 更新网格参数
        #. 考虑隔夜持仓"""
        super().onStart()

        self.write_log("=====>>>>> 盘前处理程序")
        # filepath = 'C:\\Users\\hxie\\AppData\\Roaming\\InfiniTrader_Simulation\\pyStrategy\\files\\'
        filepath = 'C:\\Users\\Administrator\\AppData\\Roaming\\InfiniTrader_Simulation\\pyStrategy\\files\\SRGrid.json'
        self.strategy_params, self.grid_overnight, self.grid_base = self.initialize_information(filepath)

        self.initialize_information()
        self.initialize_variables()

        self.write_log("=====>>>>> 进入交易程序")


    def onTick(self, tick):
        """收到行情TICK推送（必须由用户继承实现）"""
        super().onTick(tick)
        if tick.lastPrice == 0 or tick.askPrice1 == 0 or tick.bidPrice1 == 0:
            return
        self.putEvent()

        # 非交易时间直接返回
        if not self.timer.check_time(tick.time, 'trade_time'):
            self.write_log(f'{tick.time} 为非交易时间')
            return

        # 确定当前交易方向
        if (tick.askPrice1 <= self.strategy_params["short_max_price"]) & (tick.askPrice1 >= self.strategy_params["short_min_price"]):
            self.grid_direction = DIRECTION_SHORT
        elif (tick.bidPrice1 <= self.strategy_params["long_max_price"]) & (tick.askPrice1 >= self.strategy_params["long_min_price"]):
            self.grid_direction = DIRECTION_LONG
        else:
            self.write_log(f"当前价格 {tick.askPrice1} | {tick.bidPrice1} 不在任何网格方向上")
            return
                

        # 隔夜持仓处理程序
        self.process_close_overnight(tick.time)

        # 尾盘程序
        self.process_end(tick.time)

        # 正常交易
        self.calc_open_grids(tick.askPrice1, tick.bidPrice1)

        # 判断平仓单
        self.send_close_order(tick.askPrice1, tick.bidPrice1)


    def onOrder(self, order, log=False):
        """委托回报"""
        if order is None:
            return
        self.write_log("\n【委托回报】{} | 时间: {} | 委托编号: {} | 方向: {} | 开平: {} | 状态: {} | 价格: {} | 下单数量: {} | 成交数量: {}".format(
            order.symbol, order.orderTime, order.orderID, order.direction, order.offset, order.status, order.price, order.totalVolume, order.tradedVolume
        ))

        # 底仓交易的单子
        if ((order.offset == '开仓')
            & (order.direction == '空')
            & (order.price < self.strategy_parameters.short_min_price)):
            self.variables.base_orders[order.orderID].update_order_info(
                order.tradedVolume, order.status, order.orderTime
            )
            self.save_position("base")
        elif ((order.offset == '开仓')
              & (order.direction == '多')
              & (order.price > self.strategy_parameters.long_max_price)):
            self.variables.base_orders[order.orderID].update_order_info(
                order.tradedVolume, order.status, order.orderTime
            )
            self.save_position("base")
        # 网格交易的单子
        if order.orderID in self.variables.open_orders:
            self.variables.open_orders[order.orderID].update_order_info(
                order.tradedVolume, order.status, order.orderTime
            )
            self.save_position("grid")
        if order.orderID in self.variables.close_orders:
            self.variables.close_orders[order.orderID].update_order_info(
                order.tradedVolume, order.status, order.orderTime
            )
            self.save_position("grid")

        if order.status == '已撤销':
            # 撤单只会是盘中进行，而撤单后要更新下一个开仓或平仓信息
            if order.offset == '开仓':
                direction = DIRECTION_SHORT if order.direction == '空' else DIRECTION_LONG
                self.update_next_open_and_close(direction)
            elif order.offset == '平仓':
                direction = DIRECTION_LONG if order.direction == '空' else DIRECTION_SHORT
                self.update_next_open_and_close(direction)


    def onTrade(self, trade, log=True):
        """成交回报"""
        self.write_log("\n【成交回报】{} | 时间: {} | 成交编号: {} | 委托编号: {} | 方向: {} | 开平: {} | 价格: {} | 数量: {}".format(
            trade.symbol, trade.tradeTime, trade.orderID, trade.orderID, trade.direction, trade.offset, trade.price, trade.volume
        ))

        # 集合竞价（非交易时间）的成交，将其作为隔夜持仓处理
        if not self.timer.check_time(trade.tradeTime, 'trade_time'):
            keys = map(int, self.variables.open_orders.keys())
            min_key = min(keys)
            order_info = self.variables.open_orders[trade.orderID]
            order_info.order_id = min_key-1
            self.variables.open_orders[order_info.order_id] = order_info
            del self.variables.open_orders[trade.orderID]

        # 底仓特殊处理
        if trade.orderID in self.variables.base_orders:
            self.variables.base_orders[trade.orderID].update_traded_order(
                trade.tradeID, trade.price, trade.volume, trade.tradeTime
            )
            self.write_log(f"\n order id: {trade.orderID}, trade id: {trade.tradeID} is base position, no need to future operation.")
            self.save_position("base")
            return

        # 开仓单：
        if (trade.offset == "开仓") & (trade.orderID in self.variables.open_orders):
            self.variables.open_orders[trade.orderID].update_traded_order(
                trade.tradeID, trade.price, trade.volume, trade.tradeTime
            )
            # 执行平仓逻辑
            self.close_grid(trade)
            
            # 但凡有一个开仓单成交，说明我们要更新平仓单的信息了
            cancels = self.cancel_close_after_open_trades()
            if cancels == 0:
                # 如果不用撤单，则直接更新下一个开仓和平仓的信息，不需要等到撤单完成后再进行
                direction = DIRECTION_SHORT if trade.direction == '空' else DIRECTION_LONG
                self.update_next_open_and_close(direction)
        # 平仓单：
        if (trade.offset == "平仓") & (trade.orderID in self.variables.close_orders):
            # 更新平仓单的成交信息
            self.variables.close_orders[trade.orderID].update_traded_order(
                trade.tradeID, trade.price, trade.volume, trade.tradeTime
            )
            # 平仓单全部成交，则将网格加回到相关存储器中
            for _, v in self.variables.open_orders.items():
                open_id = v.find_open_for_close(trade.orderID)
                if open_id is not None:
                    break
            # TODO: 开仓单中的平仓单的信息并没有更新？为什么这里能判断一个开仓单被平仓了呢？
            if self.variables.open_orders[open_id].is_grid_closed():
                # 当平仓单全部成交后，需要更新下一个开仓单的信息，所以进行撤单
                cancels = self.cancel_open_after_close_trades()
                if cancels == 0:
                    # 如果不用撤单，则直接更新下一个开仓和平仓的信息，不需要等到撤单完成后再进行
                    direction = DIRECTION_LONG if trade.direction == '空' else DIRECTION_SHORT
                    self.update_next_open_and_close(direction)
            self.save_position("grid")


    def write_log(self, msg):
        """打印日志"""
        self.output(msg)

    def initialize_information(self, filepath: str) -> None:
        """初始化信息"""
        try:
            with open(filepath, 'r') as file:
                data = json.load(file)
        except FileNotFoundError:
            self.write_log("未找到相关文件: ", filepath)
        self.strategy_params = data['strategy_parameters']       # 策略信息
        if len(data['grid_short_overnight']) != 0:      # 做空的隔夜网格线
            self.gridlines[DIRECTION_SHORT] = data['grid_short_overnight']
        if len(data['grid_long_overnight']) != 0:       # 做多的隔夜网格线
            self.gridlines[DIRECTION_LONG] = data['grid_long_overnight']

        # 隔夜网格信息中只记录每个网格线对应的数量，但在实际交易中，我们还需要开平仓的订单编号、委托数量、成交数量等
        # 来映射网格开仓和平仓之间的关系，因此，这里初始化隔夜持仓中的这些信息
        for direction, details in self.gridlines.items():
            if len(details) == 0:
                continue
            new_dict = {}
            for i, (grid, volume) in enumerate(details.items()):
                new_dict[grid] = {'order_id': -(i+1), 'order_volume': volume, 'trade_volume': volume, 'close_id': []}
            self.gridlines[direction] = new_dict

    def initialize_variables(self) -> None:
        """初始化变量"""
        # 网格线
        if (self.strategy_params['short_max_price'] != 0) and (self.strategy_params['short_min_price'] != 0):
            if len(self.gridlines[DIRECTION_SHORT]) != 0:
                price = max(self.gridlines[DIRECTION_SHORT].keys())
                self.update_grid_info(DIRECTION_SHORT, price, self.strategy_params['grid_interval'])
        elif (self.strategy_params['long_max_price'] != 0) and (self.strategy_params['long_min_price'] != 0):
            if len(self.gridlines[DIRECTION_LONG]) != 0:
                self.current_grid = min(self.gridlines[DIRECTION_LONG].keys())
                self.update_grid_info(DIRECTION_LONG, price, self.strategy_params['grid_interval'])

    def update_grid_info(
            self, direction: int, price: float, interval: int,
            order_id: Optional[int]=None, order_volume: Optional[int]=None, trade_volume: Optional[int]=None
        ) -> None:
        """更新网格信息"""
        self.current_grid = price
        self.next_grid = price + interval if direction == DIRECTION_SHORT else price - interval
        sorted_keys = sorted(self.gridlines[direction].keys())
        self.last_grid = sorted_keys[-2] if direction == DIRECTION_SHORT else sorted_keys[1]
        self.write_log("方向：{}, 上一个网格: {}, 当前网格: {}, 下一个网格: {}".format(
            '做空' if direction==DIRECTION_SHORT else '做多',
            self.last_grid, self.current_grid, self.next_grid
        ))

        if order_id is None:
            return      # 如果传入了order_id，说明要更新 self.gridlines 中的已开仓网格信息
        
        """
        开仓 & 网格不记录 -> 新开仓网格，则新增记录
        开仓 & 网格已记录 -> 已开仓网格，更新记录
        平仓 -> 找到对应的开仓网格，更新记录
        """
        if price not in self.gridlines[direction]:
            self.gridlines[direction][price] = {'order_id': order_id, 'order_volume': order_volume, 'trade_volume': trade_volume, 'close_id': []}
        else:
            self.grid
        
        

    def deal_grid_overnight(self, curtime: str):
        """处理隔夜持仓"""
        if (self.tag_of_execute_process_closeOvernight is True) | (not self.timer.check_time(curtime, 'trade_time'))
            return
    
    
    def opening(self, curr_ask: float, curr_bid: float):
        """开仓逻辑"""
        if ((self.grid_direction == DIRECTION_SHORT) &
            (curr_ask >= self.next_grid - self.strategy_params["trigger_shift"])):
            self.send_order(symbol=self.vtSymbol, exchange=self.exchange, direction=DIRECTION_SHORT,
                offset=OFFSET_OPEN, price=self.next_grid, volume=self.strategy_params['order_qty'])
        if ((self.grid_direction == DIRECTION_LONG) &
            (curr_bid <= self.next_grid + self.strategy_params["trigger_shift"])):
            self.send_order(symbol=self.vtSymbol, exchange=self.exchange, direction=DIRECTION_LONG,
                offset=OFFSET_OPEN, price=self.next_grid, volume=self.strategy_params['order_qty'])


    def closing(self):
        """平仓逻辑"""


    def send_order(self, symbol, exchange, direction, offset, price, volume):
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
        if self.manage_risk():
            self.write_log(f"【触发风控】不予发送委托单! Direction: {direction}, Offset: {offset}, price: {price}, volume: {volume}")
            return

        if (direction == DIRECTION_SHORT) & (offset == OFFSET_OPEN):
            order_type = CTAORDER_SHORT      # 卖开
        elif (direction == DIRECTION_LONG) & (offset == OFFSET_OPEN):
            order_type = CTAORDER_BUY      # 买开
        elif (direction == DIRECTION_SHORT) & (offset == OFFSET_CLOSE):
            order_type = CTAORDER_SELL      # 卖平
        elif (direction == DIRECTION_LONG) & (offset == OFFSET_CLOSE):
            order_type = CTAORDER_COVER     # 买平
        else:
            self.write_log(f"无法确定下单类型! Direction: {direction}, Offset: {offset}")
            order_type = CTAORDER_COVER
        # 发的是 GFD 指令
        order_id = self.sendOrder(orderType=order_type, price=price, volume=volume, symbol=symbol, exchange=exchange)
        self.write_log("\n【发委托单】合约: {}, 委托编号: {}, 买卖: {}, 开平: {}, 价格: {}, 数量: {}".format(
            symbol, order_id, '做空' if direction==DIRECTION_SHORT else '做多',
            '开仓' if offset==OFFSET_OPEN else '平仓', price, volume))

        # 更新网格信息
        if offset == OFFSET_OPEN:
            self.update_grid_info(
                direction=direction, price=price, interval=self.strategy_params["grid_interval"],
                order_id=order_id
            )



class timer:
    """时间相关"""
    def __init__(self):
        # 早盘1开始时间
        self.am_h1_start = '09:00:00'
        # 早盘1结束时间
        self.am_h1_end = '10:15:00'
        # 早盘2开始时间
        self.am_h2_start = '10:30:00'
        # 早盘2结束时间
        self.am_h2_end = '11:30:00'
        # 下午开始时间
        self.pm_start = '13:30:00'
        # 下午结束时间
        self.pm_end = '15:00:00'
        # 夜盘开始时间
        self.night_start = '21:00:00'
        # 夜盘结束时间
        self.night_end = '23:00:00'
        # 程序启动开始时间
        self.start_start = '19:00:00'
        # 程序启动结束时间
        self.start_end = '20:59:00'
        # 集合竞价开始时间
        self.auction_start = '20:55:00'
        # 集合竞价结束时间
        self.auction_end = '20:58:55'
        # 撤单时间
        # 尾盘开始时间
        self.end_start = '14:59:55'
        # 尾盘结束时间
        self.end_end = '15:00:00'

    def get_cancel_time(self, start_time: str):
        """获取撤单时间"""
        start_time = datetime.strptime(start_time, '%H:%M:%S')
        cancel_start = start_time + timedelta(minutes=5)
        cancel_end = start_time + timedelta(minutes=6)
        return cancel_start.strftime("%H:%M:%S"), cancel_end.strftime("%H:%M:%S")

    def check_time(self, curtime: str, flag: str) -> bool:
        """检查当前时间是否在特定的时间段内"""
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
                cancel_start, cancel_end = self.get_cancel_time(i)
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

