from ctaBase import *
from ctaTemplate import *

from datetime import datetime, timedelta
from datetime import time as datetime_time
from typing import Union, Tuple, List, Optional, Dict
import pandas as pd
import numpy as np
import time
import copy
import random

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

CLOSE_ADVANCED = 'advanced'
CLOSE_PROMPTLY = 'promptly'

"""
Logs 2024-1-9
1. 增加风控模块
2. 修复发0手单数的问题
"""

class future_grid_inf_v106(CtaTemplate):
    """期货网格策略"""
    vtSymbol = ''
    exchange = ''
    className = 'future_grid_inf_v106'
    author = 'hxgre'
    name = EMPTY_UNICODE                # 策略实例名称

    # # 参数映射表
    # paramMap = {
    #     'exchange': '交易所',
    #     'vtSymbol': '合约',
    #     'account_id': '账户ID',
    #     'position_filename': '隔夜持仓',
    #     'strategy_filename': '策略参数',
    # }
    # # 参数列表，保存了参数的名称
    # paramList = list(paramMap.keys())

    # # 变量映射表
    # varMap   = {
    #     'trading' : '交易中',
    #     'pos': '仓位',
    #     'curr_grid': '当前网格',
    #     'next_open': '开仓点',
    #     'next_close': '平仓点',
    # }
    # # 变量列表，保存了变量的名称
    # varList = list(varMap.keys())


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
            'curr_grid': '当前网格',
            'next_open': '开仓点',
            'next_close': '平仓点',
        }

        # 输入参数参数
        self.exchange = ''
        self.vtSymbol = ''
        self.account_id = '30501598'
        self.position_filename = 'posSR405.h5'
        self.strategy_filename = 'paraSR405.csv'

        self.strategy_parameters = strategy_parameters()
        self.variables = variables()
        self.timer = timer()
        self.risker = risk_control()

        self.curr_grid = 0
        self.next_open = 0
        self.next_close = 0

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

        # 读取相关信息
        # filepath = 'C:\\Users\\hxie\\AppData\\Roaming\\InfiniTrader_Simulation\\pyStrategy\\files\\'
        filepath = 'C:\\Users\\Administrator\\AppData\\Roaming\\InfiniTrader_Simulation\\pyStrategy\\files\\'
        self.variables.input_filename(filepath, self.position_filename, self.strategy_filename)
        self.write_log(f"FILE PATH: \n {self.variables.position_filepath}, \n {self.variables.strategy_filepath}")
        self.write_log("=====>>>>> 盘前处理程序")
        self.read_strategy_info()
        self.read_overnight_position()
        self.read_base_position()

        self.initial_gridlines()

        # self.wait_for_auction()
        # self.process_auction(datetime.now().strftime("%H:%M:%S"))

        # 注册一个60分钟触发一次的定时器（1s = 1000 ms）
        millisecond = 60*60*1000
        self.regTimer(1, millisecond)

        self.write_log("=====>>>>> 进入交易程序")

    def onTick(self, tick):
        """收到行情TICK推送（必须由用户继承实现）"""
        super().onTick(tick)
        # 过滤涨跌停和集合竞价
        if tick.lastPrice == 0 or tick.askPrice1 == 0 or tick.bidPrice1 == 0:
            return
        # 更新时间，推送状态
        self.putEvent()

        # 非交易时间直接返回
        if not self.timer.check_time(tick.time, 'trade_time'):
            self.write_log(f'{tick.time} is not in trading time')
            return
        # 隔夜持仓处理程序
        self.process_close_overnight(tick.time)

        # 撤单程序？
        # self.process_cancel(tick.time, tick.askPrice1, tick.bidPrice1)
        
        # 尾盘程序
        self.process_end(tick.time)

        # 增加底仓
        # self.add_base_grids(tick.askPrice1, tick.bidPrice1)

        # 单边网格中，边界网格被平仓后的处理
        self.after_liquidation(tick.askPrice1, tick.bidPrice1)

        # 正常交易
        self.calc_open_grids(tick.askPrice1, tick.bidPrice1)

        # 判断平仓单
        # BUG: 2024-01-09，开仓单还没有成交就发平仓单了
        self.send_close_order(tick.askPrice1, tick.bidPrice1)

        self.curr_grid = self.variables.gridlines[DIRECTION_LONG]['curr_grid']
        self.next_open = self.variables.gridlines[DIRECTION_LONG]['next_grid']
        self.next_close = self.variables.close_info[DIRECTION_LONG]['next_close_grid']

    def onTimer(self, tid: int) -> None:
        """收到定时推送"""
        if tid == 1:
            now_time = datetime.now()
            if not self.timer.check_time(now_time.strftime("%Y%m%d %H:%M:%S"), 'trade_time'):
                self.write_log(f'onTimer: {now_time} is not in trading time')
                return
            now_time = datetime.now()
            start_date = now_time.strftime('%Y%m%d')
            start_time = now_time.strftime('%H:%M:%S')
            # 获取从 start_date 和 start_time 往前 1 天的 5 分钟 K线
            bars: list = ctaEngine.getKLineData(
                self.vtSymbol,
                self.exchange,
                start_date,
                1,
                0,
                start_time,
                5
            )
            if not bars:
                self.write_log(f'{self.vtSymbol} 合约在所选周期内没有分钟线数据')
            
            # 计算指标
            df = pd.DataFrame(bars)
            if df.shape[0] < 36:
                self.write_log(f'Error! the data is not enough! \n {df}')
            indicate = indicator(df)
            indicate.run()
            if indicate.params['interval'] == 0:
                self.write_log(" =====>>>>> TEST")
                self.write_log(indicate.msg)
                return
            
            # 是否更新网格间距
            if int(self.strategy_parameters.grid_interval) == int(indicate.params['interval']):
                self.write_log(
                    "No need to update the grid interval. "
                    + f"old grid interval: {self.strategy_parameters.grid_interval}, "
                    + f"new grid interval: {indicate.params['interval']}."
                )
                self.test_flag = False
                return

            # 更新参数
            # self.strategy_parameters.update_grid_interval(indicate.params['interval'])
            self.strategy_parameters.update_grid_interval(4)
            # self.strategy_parameters.update_close_short(indicate.params['interval'])
            self.strategy_parameters.update_close_short(4)
            # self.strategy_parameters.update_close_long(indicate.params['interval'])
            self.strategy_parameters.update_close_long(4)
            self.write_log(
                f"Update grid interval. interval: {self.strategy_parameters.grid_interval}, "
                + f"close_short: {self.strategy_parameters.close_short}, "
                + f"close_long: {self.strategy_parameters.close_long}."
            )
            self.strategy_parameters.save_parameters(self.variables.strategy_filepath)
            self.write_log(self.strategy_parameters.print_parameters())

            # 修改了网格间距，则将所有开仓的挂单撤单
            self.cancel_after_change_params()

    def onOrder(self, order, log=False):
        """委托回报"""
        if order is None:
            return
        self.write_log(
            f"\n Return order information. Direction: {order.direction} | "
            + f"Offset: {order.offset} | order id: {order.orderID} | "
            + f"price: {order.price} | total volume: {order.totalVolume} | "
            + f"traded volume: {order.tradedVolume} | "
            + f"status: {order.status} | time: {order.orderTime}."
        )
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
        self.write_log(
            f"\n Return trade information. Direction: {trade.direction} | Offset: {trade.offset} | "
            + f"order id: {trade.orderID} | trade id: {trade.tradeID} | price: {trade.price} | "
            + f"volume: {trade.volume} | commission: {trade.commission} | time: {trade.tradeTime}."
        )

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

    def read_strategy_info(self):
        """读取策略信息"""
        try:
            strate_info = pd.read_csv(self.variables.strategy_filepath, encoding='GBK')
            strate_info['date'] = pd.to_datetime(strate_info['date'], format='%Y/%m/%d')
            strate_info['date'] = strate_info['date'].dt.strftime('%Y-%m-%d')
        except FileNotFoundError:
            self.write_log("\n No strategy information for the previous day.")
        latest_info = strate_info.iloc[-1,:]
        strategy_dict = latest_info.to_dict()
        self.strategy_parameters.from_dict(strategy_dict)
        self.write_log('\n Read strategy info before trading.')
        self.write_log(self.strategy_parameters.print_parameters())

    def read_overnight_position(self,):
        """读取隔夜网格数据"""
        try:
            overnight = pd.read_hdf(self.variables.position_filepath,
                                    key=self.variables.overnight_key)
        except (FileNotFoundError, KeyError):
            overnight = None
        if (overnight is None) or (overnight.shape[0] == 0):
            self.write_log('\n 【隔夜持仓】没有隔夜持仓！')
        else:
            for i in range(0, overnight.shape[0]):
                order_info = grid_open_order()
                order_info.from_dict(overnight.iloc[i,:].to_dict())
                order_info.order_id = i*(-1) - 1
                self.variables.open_orders[order_info.order_id] = order_info
                if order_info.direction == DIRECTION_SHORT:
                    self.variables.overnight_gridlines[DIRECTION_SHORT].append(order_info.order_price)
                elif order_info.direction == DIRECTION_LONG:
                    self.variables.overnight_gridlines[DIRECTION_LONG].append(order_info.order_price)
            # 统计隔夜持仓的数据
            total_volume = 0
            grids = []
            for _, v in self.variables.open_orders.items():
                total_volume += v.traded_volume
                grids.append(v.order_price)
            self.write_log(f"\n 【隔夜持仓】隔夜持仓数量: {total_volume} \n 已开仓网格: {grids}.")

    def save_position(self, _type: str="grid"):
        """保存持仓信息"""
        if _type == "grid":
            orders_dict = self.variables.open_orders
            _key = self.variables.overnight_key
        elif _type == "base":
            orders_dict = self.variables.base_orders
            _key = self.variables.base_key
        orders = []
        for _, v in orders_dict.items():
            if v.is_grid_closed():
                continue
            if v.traded_volume == 0:
                continue
            orders.append(v)
        df = pd.DataFrame({
            'direction': [order.direction for order in orders],
            'offset': [order.offset for order in orders],
            'order_id': [order.order_id for order in orders],
            'order_price': [order.order_price for order in orders],
            'order_volume': [order.order_volume for order in orders],
            'traded_price': [order.traded_price for order in orders],
            'traded_volume': [order.traded_volume for order in orders],
            'order_time': [order.order_time for order in orders],
            'status': [order.status for order in orders],
        })
        if df.shape[0] == 0:
            return
        if _type == 'base':
            volume = df['traded_volume'].sum()
            price = (df['traded_price'] * df['traded_volume']).sum() / volume
            new_df = df.iloc[-1, :]
            new_df.update({
                'order_price': round(price, 2),
                'order_volume': int(volume),
                'traded_price': round(price, 2),
                'traded_volume': int(volume)
            })
            pd.DataFrame(new_df).T.to_hdf(self.variables.position_filepath, key=_key, encoding='GBK')
        else:
            df.to_hdf(self.variables.position_filepath, key=_key)
        self.write_log(f"\n Saving position information. {self.variables.position_filepath}, {_key}")

        store = pd.HDFStore(self.variables.position_filepath)
        store.close()

    def read_base_position(self):
        """读取底仓数据"""
        try:
            base = pd.read_hdf(self.variables.position_filepath,
                               key=self.variables.base_key)
        except (FileNotFoundError, KeyError):
            base = None
        if (base is None) or (base.shape[0] == 0):
            self.write_log('\n 【底仓数据】：没有底仓！')
        else:
            for i in range(0, base.shape[0]):
                order_info = grid_open_order()
                order_info.from_dict(base.iloc[i,:].to_dict())
                # 第一笔交易的单子的order_id为0，所以这里不能为0
                order_info.order_id = i*(-1) - 1000
                self.variables.base_orders[order_info.order_id] = order_info
            total_volume = 0
            for _, v in self.variables.base_orders.items():
                total_volume += v.traded_volume
            self.write_log(f"\n 【底仓数据】已有底仓数量: {total_volume}.")

    def initial_gridlines(self):
        """初始化价差网格"""
        # 做空
        if (self.strategy_parameters.short_max_price != 0) | (self.strategy_parameters.short_min_price != 0):
            if len(self.variables.overnight_gridlines[DIRECTION_SHORT]) > 0:
                self.strategy_parameters.update_short_min_price(
                    min(
                        *self.variables.overnight_gridlines[DIRECTION_SHORT],
                        self.strategy_parameters.short_min_price
                    )
                )
                price = max(self.variables.overnight_gridlines[DIRECTION_SHORT])
            else:
                price = self.strategy_parameters.short_min_price
            self.variables.update_gridlines(
                direction=DIRECTION_SHORT,
                price=price,
                interval=self.strategy_parameters.grid_interval,
                init_price=self.strategy_parameters.short_min_price
            )
            self.write_log(f'update grid lines: {self.variables.gridlines}')
        # 做多
        if (self.strategy_parameters.long_max_price != 0) | (self.strategy_parameters.long_min_price != 0):
            if len(self.variables.overnight_gridlines[DIRECTION_LONG]) > 0:
                self.strategy_parameters.update_long_max_price(
                    max(
                        *self.variables.overnight_gridlines[DIRECTION_LONG],
                        self.strategy_parameters.long_max_price
                    )
                )
                price = min(self.variables.overnight_gridlines[DIRECTION_LONG])
            else:
                price = self.strategy_parameters.long_max_price
            self.variables.update_gridlines(
                direction=DIRECTION_LONG,
                price=price,
                interval=self.strategy_parameters.grid_interval,
                init_price=self.strategy_parameters.long_max_price
            )
            self.write_log(f'\n Update grid lines: {self.variables.gridlines}')
        self.strategy_parameters.save_parameters(self.variables.strategy_filepath)
        self.write_log(f'\n 初始化后的网格参数: \n {self.strategy_parameters.print_parameters()}')

    def wait_for_auction(self):
        """程序启动后等待集合竞价程序"""
        curtime = datetime.now().strftime('%H:%M:%S')
        if self.timer.check_time(curtime, 'start'):
            while not self.timer.check_time(curtime, 'auction'):
                self.write_log(f'{curtime} is not in the auction time, please wait for 60 seconds!')
                time.sleep(60)
                curtime = datetime.now().strftime('%H:%M:%S')

    def process_auction(self, curtime: str):
        """集合竞价程序"""
        if ((self.variables.process_auction_executed is True)
            | (not self.timer.check_time(curtime, 'auction'))):
            return
        self.write_log("\n Enter the process of auction. #####################################")
        grid_lines = self.variables.gridlines[DIRECTION_SHORT].copy()
        for price in grid_lines:
            order_info = self.send_order({
                'direction': DIRECTION_SHORT,
                'offset': OFFSET_OPEN,
                'price': price,
                'volume': self.strategy_parameters.order_qty,
            })
            self.variables.open_orders[order_info.order_id] = order_info
        grid_lines = self.variables.gridlines[DIRECTION_LONG].copy()
        for price in grid_lines:
            order_info = self.send_order({
                'direction': DIRECTION_LONG,
                'offset': OFFSET_OPEN,
                'price': price,
                'volume': self.strategy_parameters.order_qty,
            })
            self.variables.open_orders[order_info.order_id] = order_info
        self.save_position("grid")
        self.variables.process_auction_executed = True
        self.write_log("\n Finish the process of auction. #####################################")

    def process_close_overnight(self, curtime: str):
        """处理隔夜持仓，包含以下两种形式：
        1. 提前挂单
        2. 及时发单
        """
        cond_1 = self.variables.process_close_overnight_executed is True
        cond_2 = not self.timer.check_time(curtime, 'trade_time')
        if cond_1 | cond_2:
            return
        self.write_log('\n ############### 处理隔夜持仓 ###############')
        self.close_grid(method='overnight')
        self.save_position("grid")
        self.variables.process_close_overnight_executed = True
        self.write_log('\n ############### 处理隔夜持仓 ###############')

    def process_cancel(self, curtime: str, curr_ask: float, curr_bid: float):
        """撤单程序"""
        if ((self.variables.process_cancel_executed is False)
            | (not self.timer.check_time(curtime, 'cancel_time'))):
            return
        self.write_log('\n Enter the process of cancel. #####################################')
        for k, v in self.variables.open_orders.items():
            if v.status not in [STATUS_NOTTRADED, STATUS_PARTTRADED]:
                continue
            if (v.direction == DIRECTION_SHORT) | (
                v.order_price <= (
                    curr_ask + (self.strategy_parameters.cancel_parameter
                                * max(self.strategy_parameters.grid_interval))
                    )
                ):
                continue
            if (v.direction == DIRECTION_LONG) | (
                v.order_price >= (
                    curr_bid - (self.strategy_parameters.cancel_parameter
                                * max(self.strategy_parameters.grid_interval))
                    )
                ):
                continue
            self.cancelOrder(k)
            self.write_log(f"\n Cancel order: {k}, grid: {v.order_price}")
        self.save_position("grid")
        self.variables.process_cancel_executed = True
        self.write_log('\n Finish the process of cancel. #####################################')

    def process_end(self, curtime: str):
        """尾盘程序"""
        if ((self.variables.process_end_executed is True)
            | (not self.timer.check_time(curtime, "end"))):
            return
        self.write_log('\n Enter the process of end. #####################################')
        account_balance = self.get_investor_account(self.account_id).balance
        self.strategy_parameters.update_account_balance(account_balance)
        self.strategy_parameters.save_parameters(self.variables.strategy_filepath)
        self.save_position("grid")
        self.save_position("base")
        self.variables.process_end_executed =True
        self.write_log('\n Finish the process of end. #####################################')


    #################### 发单逻辑 ####################
    def calc_open_grids(self, curr_ask: float, curr_bid: float):
        """获取开仓网格"""
        if ((self.variables.gridlines[DIRECTION_SHORT]['next_grid'] !=0 ) &
            (curr_ask >= self.variables.gridlines[DIRECTION_SHORT]['next_grid'] - self.strategy_parameters.tri_shift)):
            self.update_qty(self.variables.gridlines[DIRECTION_LONG]['next_grid'])
            if self.risk_check(DIRECTION_SHORT):
                order_info = self.send_order({
                    'direction': DIRECTION_SHORT,
                    'offset': OFFSET_OPEN,
                    'price': self.variables.gridlines[DIRECTION_SHORT]['next_grid'],
                    'volume': self.strategy_parameters.order_qty,
                })
                self.variables.open_orders[order_info.order_id] = order_info
        if ((self.variables.gridlines[DIRECTION_LONG]['next_grid'] !=0 ) &
            (curr_bid <= self.variables.gridlines[DIRECTION_LONG]['next_grid'] + self.strategy_parameters.tri_shift)):
            self.update_qty(self.variables.gridlines[DIRECTION_LONG]['next_grid'])
            if self.risk_check(DIRECTION_LONG):
                order_info = self.send_order({
                    'direction': DIRECTION_LONG,
                    'offset': OFFSET_OPEN,
                    'price': self.variables.gridlines[DIRECTION_LONG]['next_grid'],
                    'volume': self.strategy_parameters.order_qty,
                })
                self.variables.open_orders[order_info.order_id] = order_info

    def update_qty(self, curr_grid: float):
        """按照100个点100手计算该网格的发单数量"""
        # 确定当前价格所在区间
        upper_limit = (int(curr_grid/100)+1)*100
        lower_limit = (int(curr_grid/100))*100
        vol_sum = 0
        range_grids = []    # 区间内的网格
        for _, v in self.variables.open_orders.items():
            if v.is_grid_closed():
                # 如果这个网格线已经被平了，则跳过
                continue
            if (v.order_price < lower_limit) | (v.order_price > upper_limit):
                # 如果不在这个区间，则跳过
                continue
            if v.status == STATUS_CANCELLED:
                # 如果撤单，也跳过
                continue
            if v.status == STATUS_PARTTRADED_PARTCANCELLED:
                # 如果部成部撤，则只计算成交部分
                vol_sum += v.traded_volume
                range_grids.append(v.order_price)
                continue
            # 其他订单形式，则计算下单数量
            vol_sum += v.order_volume
            range_grids.append(v.order_price)
        remaining_qty = 200 - vol_sum
        curr_interval = self.strategy_parameters.grid_interval
        if len(range_grids) == 0:
            min_range_grid = upper_limit
        else:
            min_range_grid = min(range_grids)
        # self.write_log(f"\n curr price: {curr_grid}, vol_sum: {vol_sum}, min_grid: {min_range_grid}, lower_limit: {lower_limit} remaining qty: {remaining_qty}, curr interval: {curr_interval}")
        new_qty = int(remaining_qty / ((min_range_grid - lower_limit) / curr_interval))
        new_qty = 5 if new_qty < 3 else new_qty
        # if self.strategy_parameters.order_qty != new_qty:
            # self.write_log(f'Update order qty: {new_qty}')
        new_qty = 12
        self.strategy_parameters.update_order_qty(new_qty)

    def send_order(self, place_order: dict):
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
        if place_order['offset'] == OFFSET_OPEN:
            order_info = grid_open_order()
        elif place_order['offset'] == OFFSET_CLOSE:
            order_info = grid_close_order()
        order_info.place_order(
            place_order['direction'], place_order['offset'],
            place_order['price'], place_order['volume']
        )
        # 发的是 GFD 指令
        order_id = self.sendOrder(
            order_info.order_type,
            order_info.order_price,
            order_info.order_volume,
            self.vtSymbol,
            self.exchange,
        )
        order_info.order_id = order_id
        self.write_log(f"\n Send order. ID: {order_id}. order_info: {place_order}.")
        # 更新网格信息
        if order_info.offset == OFFSET_OPEN:
            self.variables.update_gridlines(
                direction=place_order['direction'],
                price=order_info.order_price,
                interval=self.strategy_parameters.grid_interval
            )
            self.write_log(f'\n 发单后更新网格线参数: {self.variables.gridlines}')
        return order_info

    def risk_check(self, direction: int) -> bool:
        """风控"""
        available = self.get_investor_account(self.account_id).available
        if direction == DIRECTION_LONG:
            limit_price = self.strategy_parameters.long_min_price
            price = self.variables.gridlines[DIRECTION_LONG]['next_grid']
        elif direction == DIRECTION_SHORT:
            limit_price = self.strategy_parameters.short_max_price
            price = self.variables.gridlines[DIRECTION_SHORT]['next_grid']
        return self.risker.check(
            order_qty = self.strategy_parameters.order_qty,
            available_money = available,
            order_price = price,
            direction = direction,
            limit_price = limit_price,
            open_orders = self.variables.open_orders,
        )

    #################### 平仓逻辑 ####################
    def close_grid(self, trade=None, method: str='intraday'):
        """发平仓单逻辑，发平仓单的地方：
        1. 开盘时处理隔夜持仓
        2. 盘中正常交易
        """
        if method == 'intraday':
            # 盘中交易的平仓单
            if self.strategy_parameters.close_position_method == CLOSE_ADVANCED:
                self.write_log('\n 提前平仓，即开仓单成交n手，则发n手的平仓单')
                self._close_grid_advance(trade)
            elif self.strategy_parameters.close_position_method == CLOSE_PROMPTLY:
                self.write_log('\n 即时平仓，即达到平仓线再平仓')
                self._close_grid_promptly()
            else:
                self.write_log(f'请检查平仓方式: {self.strategy_parameters.close_position_method}')
                raise ValueError("请检查平仓方式")
        elif method == 'overnight':
            # 隔夜持仓的平仓单
            for k, v in self.variables.open_orders.items():
                if int(v.order_id) >= 0:     # 隔夜持仓的 order_id 统一为负数
                    continue
                if self.strategy_parameters.close_position_method == CLOSE_ADVANCED:
                    self.write_log('\n 提前平仓，即开仓单成交n手，则发n手的平仓单')
                    self._close_overnight_in_advance(k, v)
                elif self.strategy_parameters.close_position_method == CLOSE_PROMPTLY:
                    self.write_log('\n 即时平仓，即达到平仓线再平仓')
                    self._close_overnight_promptly(v)
                else:
                    self.write_log(f'请检查平仓方式: {self.strategy_parameters.close_position_method}')
                    raise ValueError("请检查平仓方式")
            # 更新下一个开仓单和平仓单的信息
            self.update_next_open_and_close(DIRECTION_SHORT)
            self.update_next_open_and_close(DIRECTION_LONG)

    def operations_after_send_close(self, open_id, order_info):
        """发平仓单后的三个操作"""
        # 增加 close_orders 的信息
        self.variables.close_orders[order_info.order_id] = order_info
        # 为开仓单增加对应平仓单的id信息
        self.variables.open_orders[open_id].add_close_order(order_info)
        # 存储网格数据
        self.save_position("grid")

    def _close_grid_advance(self, trade):
        """盘中交易-提前平仓"""
        if self.variables.open_orders[trade.orderID].direction == DIRECTION_SHORT:
            order_type = DIRECTION_LONG
            price = (self.variables.open_orders[trade.orderID].order_price
                    - self.strategy_parameters.close_short)
        elif self.variables.open_orders[trade.orderID].direction == DIRECTION_LONG:
            order_type = DIRECTION_SHORT
            price = (self.variables.open_orders[trade.orderID].order_price
                    + self.strategy_parameters.close_long)
        order_info = self.send_order({
            'direction': order_type,
            'offset': OFFSET_CLOSE,
            'price': price,
            'volume': trade.volume,
        })
        self.operations_after_send_close(trade.orderID, order_info)

    def _close_grid_promptly(self):
        """盘中交易-即时平仓，即达到平仓线再平仓"""
        pass

    def _close_overnight_in_advance(self, k, v):
        """隔夜持仓-提前平仓"""
        if v.direction == DIRECTION_SHORT:
            direction = DIRECTION_LONG
            price = v.order_price - self.strategy_parameters.close_short
        elif v.direction == DIRECTION_LONG:
            direction = DIRECTION_SHORT
            price = v.order_price + self.strategy_parameters.close_long
        order_info = self.send_order({
            'direction': direction,
            'offset': OFFSET_CLOSE,
            'price': price,
            'volume': v.traded_volume,
        })
        self.operations_after_send_close(k, order_info)

    def _close_overnight_promptly(self, v):
        """隔夜持仓-即时平仓，即达到平仓线再平仓"""
        self.write_log(f'close overnight promptly. current grid: {v.order_price}.')

    def update_nextclose(self, direction: int, open_orderID):
        """更新下一个平仓的信息"""
        if open_orderID is None:
            # 为了避免重复发平仓单，在发单后，将相关信息初始化
            self.variables.close_info[direction]['curr_open_id'] = None
            self.variables.close_info[direction]['curr_open_grid'] = 0
            self.variables.close_info[direction]['next_close_grid'] = 0
            self.variables.close_info[direction]['close_vol'] = 0
            return
        self.variables.close_info[direction]['curr_open_id'] = open_orderID
        curr_open_grid = self.variables.open_orders[open_orderID].order_price
        self.variables.close_info[direction]['curr_open_grid'] = curr_open_grid
        min_close_interval = 3       # 平仓最低6个网格
        if direction == DIRECTION_SHORT:
            close_price = curr_open_grid - max(self.strategy_parameters.close_short, min_close_interval)
        elif direction == DIRECTION_LONG:
            close_price = curr_open_grid + max(self.strategy_parameters.close_long, min_close_interval)
        self.variables.close_info[direction]['next_close_grid'] = close_price
        self.variables.close_info[direction]['close_vol'] = self.variables.open_orders[open_orderID].traded_volume

    def send_close_order(self, curr_ask: float, curr_bid: float):
        """即时平仓-盘中计算平仓信号并发单"""
        open_order_id = self.variables.close_info[DIRECTION_SHORT]['curr_open_id']
        if open_order_id is not None:
            close_price = self.variables.close_info[DIRECTION_SHORT]['next_close_grid']
            tri_price = close_price + self.strategy_parameters.tri_shift
            if curr_bid <= tri_price:
                volume = self.variables.close_info[DIRECTION_SHORT]['close_vol']
                if volume > 0:
                    # 开仓单成交后，才能发平仓单
                    order_info = self.send_order({
                        'direction': DIRECTION_LONG,
                        'offset': OFFSET_CLOSE,
                        'price': close_price,
                        'volume': volume,
                    })
                    self.operations_after_send_close(open_order_id, order_info)
                    self.update_nextclose(DIRECTION_SHORT, None)
                    self.write_log(f'Update next close order after sending a close SHORT order: {self.variables.close_info}')
        open_order_id = self.variables.close_info[DIRECTION_LONG]['curr_open_id']
        if open_order_id is not None:
            close_price = self.variables.close_info[DIRECTION_LONG]['next_close_grid']
            tri_price = close_price - self.strategy_parameters.tri_shift
            if curr_ask >= tri_price:
                volume = self.variables.close_info[DIRECTION_LONG]['close_vol']
                if volume > 0:
                    order_info = self.send_order({
                        'direction': DIRECTION_SHORT,
                        'offset': OFFSET_CLOSE,
                        'price': close_price,
                        'volume': volume,
                    })
                    self.operations_after_send_close(open_order_id, order_info)
                    self.update_nextclose(DIRECTION_LONG, None)
                    self.write_log(f'Update next close order after sending a close LONG order: {self.variables.close_info}')

    def update_next_open_and_close(self, direction: int=DIRECTION_SHORT):
        """更新开仓信息和平仓信息"""
        _key = self.find_curr_open_grid(direction)
        if _key is None:
            # 单边网格中，可能找不到另一方向的开仓单，所以直接返回
            return
        # 更新下一个平仓单的信息
        self.update_nextclose(direction, _key)
        self.write_log(f'{direction}: Update next close order: {self.variables.close_info}')
        # 更新下一个开仓单的信息
        curr_grid = self.variables.open_orders[_key].order_price
        self.variables.update_gridlines(
            direction=direction,
            price=curr_grid,
            interval=self.strategy_parameters.grid_interval
        )
        self.write_log(f'{direction}: Update next open grid: {self.variables.gridlines}.')

    def find_curr_open_grid(self, direction: int=DIRECTION_SHORT):
        """找到当前未完全平仓的开仓单中的当前网格"""
        if direction == DIRECTION_SHORT:            
            _key = self._find_max_open_grid()
        elif direction == DIRECTION_LONG:
            _key = self._find_min_open_grid()
        return _key

    def _find_max_open_grid(self):
        """找到未完全平仓中的最大网格"""
        max_grid = 0
        max_key = None
        for k, v in self.variables.open_orders.items():
            # 忽略被撤单的开仓但
            if v.direction != DIRECTION_SHORT or v.closed_status is True or v.status == STATUS_CANCELLED:
                continue
            if v.order_price > max_grid:
                max_grid = v.order_price
                max_key = k
        return max_key

    def _find_min_open_grid(self):
        """找到未完全平仓的最小网格"""
        min_grid = 99999
        min_key = None
        for k, v in self.variables.open_orders.items():
            # 忽略被撤单的开仓但
            if v.direction != DIRECTION_LONG or v.closed_status is True or v.status == STATUS_CANCELLED:
                continue
            if v.order_price < min_grid:
                min_grid = v.order_price
                min_key = k
        return min_key

    #################### 撤单逻辑 ####################
    def cancel_after_change_params(self):
        """更改网格间距后的撤单程序"""
        cancel_ids = []
        for k, v in self.variables.open_orders.items():
            if (v.offset == OFFSET_OPEN) and (v.status != STATUS_ALLTRADED) and (v.status != STATUS_CANCELLED):
                cancel_ids.append(k)
        self.write_log(f"Cancel orders after change interval: {cancel_ids}")
        for i in cancel_ids:
            self.cancelOrder(i)

    def cancel_close_after_open_trades(self) -> int:
        """在即时平仓的情况下，当开仓单成交时，因为要更新下一个平仓单的信息，所以要把挂着的平仓单撤单"""
        cancel_ids = []
        for k, v in self.variables.close_orders.items():
            if (v.offset == OFFSET_CLOSE) and (v.status != STATUS_ALLTRADED) and (v.status != STATUS_CANCELLED):
                cancel_ids.append(k)
                direction = DIRECTION_LONG if v.direction == DIRECTION_SHORT else DIRECTION_LONG
                self.update_nextclose(direction, None)
        if len(cancel_ids) > 0:
            self.write_log(f"Cancel orders after open order traded: {cancel_ids}")
        for i in cancel_ids:
            self.cancelOrder(i)
        return len(cancel_ids)

    def cancel_open_after_close_trades(self) -> int:
        """在即时平仓的情况下，当平仓单全部成交时，因为要更新下一个开仓单的信息，所以要把挂着的开仓单撤单"""
        cancel_ids = []
        for k, v in self.variables.open_orders.items():
            if (v.offset == OFFSET_OPEN) and (v.status != STATUS_ALLTRADED) and (v.status != STATUS_CANCELLED):
                cancel_ids.append(k)
        self.write_log(f"Cancel orders after close order traded: {cancel_ids}")
        for i in cancel_ids:
            self.cancelOrder(i)
        return len(cancel_ids)

    #################### 特殊处理 ####################
    def add_base_grids(self, curr_ask: float, curr_bid: float):
        """增加底仓
        方案：当价格突破限制价格时，按照仓位的 n% 增加底仓，并更新策略参数
        # 当前买一价小于做空网格的限制价格，平多了，则盈利，则增加底仓
        # 当前卖一价大于做多网格的限制价格，平多了，则盈利，则增加底仓
        """
        percent = 0.4
        next_threhold = 50

        # 没有突破，则不加仓
        flag_no_add = False
        if self.strategy_parameters.sbase_threshold != 0:
            if curr_bid >= self.strategy_parameters.sbase_threshold:
                if curr_bid > self.strategy_parameters.sbase_threshold + next_threhold:
                    self.strategy_parameters.update_sbase_threshold(
                        self.strategy_parameters.sbase_threshold+next_threhold
                    )
                    self.strategy_parameters.save_parameters(self.variables.strategy_filepath)
                    self.write_log(f'\n update base short threshold: {self.strategy_parameters.sbase_threshold}')
                flag_no_add = True
        if self.strategy_parameters.lbase_threshold != 0:
            if curr_ask <= self.strategy_parameters.lbase_threshold:
                if curr_ask < self.strategy_parameters.lbase_threshold - next_threhold:
                    self.strategy_parameters.update_lbase_threshold(
                        self.strategy_parameters.lbase_threshold-next_threhold
                    )
                    self.strategy_parameters.save_parameters(self.variables.strategy_filepath)
                    self.write_log(f'\n update base long threshold: {self.strategy_parameters.lbase_threshold}')
                flag_no_add = True
        if flag_no_add is True:
            return

        # 在执行增加底仓操作之前，要检查现有的base_orders中的单子全部成交
        for _, v in self.variables.base_orders.items():
            if v.status != STATUS_ALLTRADED:
                return

        # 突破底仓限制，按照账户资金的比例补齐底仓
        self.write_log("\n Beginning add base position")
        base_value = sum(
            v.traded_price * v.traded_volume * 10 * self.strategy_parameters.margin
            for v in self.variables.base_orders.values()
        )
        if base_value < 10:
            self.write_log(f"\n Error! please check base position! {base_value}")
            for k, v in self.variables.base_orders.items():
                self.write_log(f'{k}: {v.traded_price}, {v.traded_volume}, {self.strategy_parameters.margin}')
            return
        account_balance = self.get_investor_account(self.account_id).balance
        self.strategy_parameters.update_account_balance(account_balance)
        add_value = account_balance * percent - base_value
        if isinstance(self.strategy_parameters.grid_interval, list):
            grid_interval = max(self.strategy_parameters.grid_interval)
        else:
            grid_interval = self.strategy_parameters.grid_interval
        if curr_bid < self.strategy_parameters.sbase_threshold:
            add_volume = int(add_value / (curr_bid * 10 * self.strategy_parameters.margin))
            add_price = 0
            if add_volume > 0:
                add_price = min(
                    (self.strategy_parameters.short_min_price - 2*grid_interval),
                    curr_bid - 2*grid_interval
                )
                order_info = self.send_order({
                    'direction': DIRECTION_SHORT,
                    'offset': OFFSET_OPEN,
                    'price': add_price,
                    'volume': add_volume,
                })
                self.variables.base_orders[order_info.order_id] = order_info
            self.strategy_parameters.update_sbase_threshold(
                self.strategy_parameters.sbase_threshold-next_threhold
            )
        if curr_ask > self.strategy_parameters.lbase_threshold:
            add_volume = int(add_value / (curr_ask * 10 * self.strategy_parameters.margin))
            add_price = 0
            if add_volume > 0:
                add_price = max(
                    (self.strategy_parameters.long_max_price + 2*grid_interval),
                    curr_ask + 2*grid_interval
                )
                order_info = self.send_order({
                    'direction': DIRECTION_LONG,
                    'offset': OFFSET_OPEN,
                    'price': add_price,
                    'volume': add_volume,
                })
                self.variables.base_orders[order_info.order_id] = order_info
            self.strategy_parameters.update_lbase_threshold(
                self.strategy_parameters.lbase_threshold+next_threhold
            )
        self.write_log(
            f"\n Adding base position. price: {add_price}, volume: {add_volume}. "
            + f"Account value: {int(account_balance)}, base value: {base_value}, "
            + f"add_value: {add_value}."
        )
        if add_volume <= 0:
            return
        self.strategy_parameters.update_base_volume(self.strategy_parameters.base_vol+add_volume)
        self.strategy_parameters.save_parameters(self.variables.strategy_filepath)
        self.save_position("base")
        self.write_log(self.strategy_parameters.print_parameters())

    def after_liquidation(self, curr_ask: float, curr_bid: float):
        """网格持仓被全部平仓后的操作
        条件：只在单边网格下才触发
        """
        if ((self.variables.gridlines[DIRECTION_SHORT]['init_grid'] != 0)
            & (self.variables.gridlines[DIRECTION_LONG]['init_grid'] != 0)):
            # 双边网格无法改变网格边界
            self.write_log('\n In bilateral grid trading, cannot add grid lines')
            return
        short_threhold = (self.strategy_parameters.short_min_price
                          - self.strategy_parameters.close_short)
        long_threshold = (self.strategy_parameters.long_max_price
                          + self.strategy_parameters.close_long)
        if ((self.strategy_parameters.short_min_price != 0)
            & (curr_ask >= short_threhold)):
            return
        if ((self.strategy_parameters.long_max_price != 0)
            & (curr_bid <= long_threshold)):
            return
        self.write_log('\n In unilateral grid trading, adding grid lines')
        add_nums = 3    # 增加的网格数量
        if curr_ask < short_threhold:
            self.strategy_parameters.update_short_min_price(
                short_threhold - add_nums * max(self.strategy_parameters.grid_interval)
            )
        elif curr_bid > long_threshold:
            self.strategy_parameters.update_long_max_price(
                long_threshold + add_nums * max(self.strategy_parameters.grid_interval)
            )
        self.initial_gridlines()
        self.strategy_parameters.save_parameters(self.variables.strategy_filepath)
        self.write_log("\n In unilateral grid trading, after all grid lines being liquidated.")
        self.write_log(self.strategy_parameters.print_parameters())


class strategy_parameters:
    """策略参数"""
    def __init__(self):
        """初始化"""
        self.date = ''
        self.short_max_price = 0
        self.short_min_price = 0
        self.long_max_price = 0
        self.long_min_price = 0
        self.base_vol = 0
        self.order_qty = 0
        self.cancel_parameter = 0
        self.grid_interval = []
        self.tri_shift = 0
        self.close_short = 0
        self.close_long = 0
        self.margin = 0
        self.sbase_threshold = 0
        self.lbase_threshold = 0
        self.account_balance = 0

        self.close_position_method = 'promptly'

    def from_dict(self, data_dict: dict) -> None:
        """从字典中获取策略信息"""
        for key, value in data_dict.items():
            if hasattr(self, key):
                setattr(self, key, value)
        # 如果网格间距是字符串，则要将其转换为列表
        if isinstance(self.grid_interval, str):
            self.grid_interval = [int(i) for i in self.grid_interval.split(',')]
        if 0 in self.grid_interval:
            self.grid_interval.remove(0)
        self.grid_interval = self.grid_interval[0]

    def update_short_max_price(self, new: int):
        """更新做空网格的最高价"""
        self.short_max_price = new

    def update_short_min_price(self, new: int):
        """更新做空网格的最低价"""
        self.short_min_price = new

    def update_long_max_price(self, new: int):
        """更新做多网格的最高价"""
        self.long_max_price = new
    def update_long_min_price(self, new: int):
        """更新做多网格的最低价"""
        self.long_min_price = new

    def update_base_volume(self, new: int):
        """更新底仓网格数量"""
        self.base_vol = new

    def update_order_qty(self, new: int):
        """更新下单手数"""
        self.order_qty = new

    def update_grid_interval(self, new: list):
        """更新网格间距"""
        self.grid_interval = new

    def update_close_short(self, new: int):
        """更新平空参数"""
        self.close_short = new

    def update_close_long(self, new: int):
        """更新平多参数"""
        self.close_long = new

    def update_sbase_threshold(self, new: float):
        """更新做空底仓的限制网格"""
        self.sbase_threshold = new

    def update_lbase_threshold(self, new: float):
        """更新做多底仓的限制网格"""
        self.lbase_threshold = new

    def update_account_balance(self, new: float):
        """更新账户资金"""
        self.account_balance = new

    def save_parameters(self, file_path: str):
        """存储策略信息"""
        try:
            strate_info = pd.read_csv(file_path, encoding='GBK')
            strate_info['date'] = pd.to_datetime(strate_info['date'], format='%Y/%m/%d')
            strate_info['date'] = strate_info['date'].dt.strftime('%Y-%m-%d')
        except FileNotFoundError:
            return
        self.date = datetime.now().strftime('%Y-%m-%d')
        strate_info = strate_info[(strate_info['date'] != self.date)]
        _copy = copy.deepcopy(self.__dict__)
        strate_dict = _copy.copy()
        if isinstance(strate_dict['grid_interval'], int):
            strate_dict['grid_interval'] = [strate_dict['grid_interval']]
        if len(strate_dict['grid_interval']) == 1:
            strate_dict['grid_interval'].append(0)
        strate_dict['grid_interval'] = ','.join(str(i) for i in strate_dict['grid_interval'])
        strate_info = pd.concat(
            [strate_info, pd.DataFrame(strate_dict, index=[0])],
            axis=0)
        strate_info.to_csv(file_path, encoding='GBK', index=False)

    def print_parameters(self):
        """打印策略参数"""
        msg = "\n 策略参数如下："
        for key, value in self.__dict__.items():
            msg += f"\n {key}: {value}, "
        return msg

    def create_empty(self, file_path: str):
        """创建一个空的策略信息csv"""
        df = pd.DataFrame(columns=list(self.__dict__.keys()))
        df.to_csv(file_path, encoding='GBK')


class grid_order:
    """委托信息 - 父类"""
    def __init__(self):
        self.direction = None
        self.offset = None
        self.order_id = ''
        self.order_price = 0
        self.order_volume = 0
        self.traded_volume = 0
        self.traded_price = 0
        self.order_time = ''
        self.status = None
        self.traded_ids = []
        self.traded_prices = []
        self.traded_volumes = []
        self.traded_times = []

        self.order_type = None

    def update_traded_order(self, _id: str, p: float, v: int, t: str) -> None:
        """将成交单的信息加进来"""
        if _id not in self.traded_ids:
            self.traded_ids.append(_id)
            self.traded_prices.append(p)
            self.traded_volumes.append(v)
            self.traded_times.append(t)
        total_amount = 0
        total_volumes = 0
        for i in range(0, len(self.traded_ids)):
            total_amount += self.traded_prices[i] * self.traded_volumes[i]
            total_volumes += self.traded_volumes[i]
        self.traded_price = total_amount / total_volumes

    def from_dict(self, data_dict: dict) -> None:
        """从字典中获取订单信息"""
        for key, value in data_dict.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def place_order(self, d: int, o: int, p: int, v: int) -> None:
        """构造下单的委托信息"""
        self.direction = d
        self.offset = o
        self.order_price = p
        self.order_volume = v
        self.change_order_type()

    def change_order_type(self, flag: str='inf'):
        """转换下单类型"""
        if flag == 'inf':
            if (self.direction == DIRECTION_SHORT) & (self.offset == OFFSET_OPEN):
                self.order_type = CTAORDER_SHORT      # 卖开
            elif (self.direction == DIRECTION_LONG) & (self.offset == OFFSET_OPEN):
                self.order_type = CTAORDER_BUY      # 买开
            elif (self.direction == DIRECTION_SHORT) & (self.offset == OFFSET_CLOSE):
                self.order_type = CTAORDER_SELL      # 卖平
            elif (self.direction == DIRECTION_LONG) & (self.offset == OFFSET_CLOSE):
                self.order_type = CTAORDER_COVER      # 买平
    
    def change_status(self, flag: str='inf'):
        """转换委托状态"""
        if flag == 'inf':
            if self.status == '未成交':
                self.status = STATUS_NOTTRADED
            elif self.status == '部分成交':
                self.status = STATUS_PARTTRADED
            elif self.status == '全部成交':
                self.status = STATUS_ALLTRADED
            elif self.status == '部成部撤':
                self.status = STATUS_PARTTRADED_PARTCANCELLED
            elif self.status == '已撤销':
                self.status = STATUS_CANCELLED
            elif self.status == '拒单':
                self.status = STATUS_REJECTED
            elif self.status == '未知':
                self.status = STATUS_UNKNOWN

    def update_order_info(self, traded_volume: int, status: str, order_time: datetime):
        """更新委托信息"""
        self.traded_volume = traded_volume
        self.status = status
        self.order_time = order_time
        self.change_status()


class grid_close_order(grid_order):
    """平仓单的委托信息"""
    def __init__(self):
        super().__init__()


class grid_open_order(grid_order):
    """开仓单的委托信息"""
    def __init__(self):
        super().__init__()
        self.closed_status = False
        self.close_orders = {}

    def add_close_order(self, order_info: grid_close_order):
        """增加平仓单的委托信息"""
        self.close_orders[order_info.order_id] = order_info

    def find_open_for_close(self, order_id: str) -> Union[int, None]:
        """为平仓单找到对应的开仓单"""
        return self.order_id if order_id in self.close_orders else None

    def is_grid_closed(self) -> bool:
        """判断单个网格是否被平完"""
        total_close_vol = sum(v.traded_volume for v in self.close_orders.values())
        if total_close_vol == self.order_volume:
            self.closed_status = True
        return self.closed_status


class variables:
    def __init__(self):
        self.position_filepath = ''
        self.strategy_filepath = ''
        self.overnight_key = 'overnight'
        self.base_key = 'base'

        self.base_orders = {}
        self.open_orders = {}
        self.close_orders = {}

        self.overnight_gridlines = {
            DIRECTION_SHORT: [],
            DIRECTION_LONG: []
        }

        self.gridlines = {
            DIRECTION_SHORT: {
                'init_grid': 0,
                'curr_grid': 0,
                'next_grid': 0,
            },
            DIRECTION_LONG: {
                'init_grid': 0,
                'curr_grid': 0,
                'next_grid': 0,
            }
        }

        # 在即时平仓下，记录下一个应该被平仓的开仓单
        self.close_info = {
            DIRECTION_SHORT: {
                'curr_open_id': None,
                'curr_open_grid': 0,
                'next_close_grid': 0,
                'close_vol': 0,
            },
            DIRECTION_LONG: {
                'curr_open_id': None,
                'curr_open_grid': 0,
                'next_close_grid': 0,
                'close_vol': 0,
            },
        }

        self.base_threshold = {
            DIRECTION_SHORT: [],
            DIRECTION_LONG: []
        }

        self.process_auction_executed = False
        self.process_close_overnight_executed = False
        self.process_cancel_executed = False
        self.process_end_executed = False

    def input_filename(self, file_path, position, strategy):
        """输入文件名称"""
        self.position_filepath = file_path + position
        self.strategy_filepath = file_path + strategy

    def update_gridlines(self, direction: int, price: float, interval: int, init_price: float=None):
        """更新网格参数
        以 curr_grid 为准，根据 interval 更新 next_grid
        """
        if direction == DIRECTION_SHORT:
            if init_price is not None:
                self.gridlines[DIRECTION_SHORT]['init_grid'] = init_price
            self.gridlines[DIRECTION_SHORT]['curr_grid'] = price
            self.gridlines[DIRECTION_SHORT]['next_grid'] = price + interval
            # 如果下个网格遇到了整数点，则换一个临近的
            if (self.gridlines[DIRECTION_SHORT]['next_grid'] / 10).is_integer():
                self.gridlines[DIRECTION_SHORT]['next_grid'] -= 1
        elif direction == DIRECTION_LONG:
            if init_price is not None:
                self.gridlines[DIRECTION_LONG]['init_grid'] = init_price
            self.gridlines[DIRECTION_LONG]['curr_grid'] = price
            self.gridlines[DIRECTION_LONG]['next_grid'] = price - interval
            # 如果下个网格遇到了整数点，则换一个临近的
            if (self.gridlines[DIRECTION_LONG]['next_grid'] / 10).is_integer():
                self.gridlines[DIRECTION_LONG]['next_grid'] += 1

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


class indicator:
    """指标计算"""
    def __init__(self, data: pd.DataFrame):
        self.mtk_data = data
        self.indicate_data = pd.DataFrame()

        self.frequency = 5      # 数据频率
        hours = 3       # hours指用于计算atr的总时间范围
        self.atr_period = int((hours * 60) / self.frequency)
        self.params = {
            'interval': 0,
        }

        self.msg = ''

    def atr(self, df: pd.DataFrame, period: int) -> pd.DataFrame:
        """计算eatr指标"""
        df.sort_values('datetime', inplace=True)
        df['high_low'] = df['high'] - df['low']
        df['high_close'] = np.abs(df['high'] - df['close'].shift())
        df['low_close'] = np.abs(df['low'] - df['close'].shift())
        df['mtr'] = df[['high_low', 'high_close', 'low_close']].max(axis=1)
        df['atr'] = df['mtr'].rolling(period).mean()
        df['ematr'] = df['mtr'].ewm(
            alpha=(2/(period+1)),
            min_periods=period,
            adjust=False
        ).mean()
        self.indicate_data = df[['datetime', 'symbol', 'atr', 'ematr']].copy()

    def cpt_param(self, df: pd.DataFrame) -> None:
        """计算参数"""
        df.sort_values('datetime', inplace=True)
        interval = df['ematr'].values[-1]
        if isinstance(interval, (int, float)):
            self.params['interval'] = max(int(interval), 7)

    def check_param(self):
        """检查参数的合法性"""
        if (self.params['interval'] <= 6) and (self.params['interval'] >= 25):
            self.msg += f"The new interval ({self.params['interval']}) is illegal"
            self.params['interval'] = 0

    def run(self):
        """主函数"""
        self.atr(self.mtk_data, self.atr_period)
        self.cpt_param(self.indicate_data)
        self.check_param()


class risk_control:
    """风控模块
    
    1. 资金控制
    2. 下单手数控制
    3. 价格控制
    4. 同一网格重复发单控制
    """
    def __init__(self):
        # 检查下单手数是否合规
        self.order_qty = 0  # 下单手数

        # 检查资金是否充足
        self.available_money = 0    # 可用资金

        # 检查下单价格是否超过上下限
        self.order_price = 0   # 下单价格
        self.direction = 0    # 下单方向
        self.limit_price = 0    # 价格限制

        # 检查同一网格线是否重复发单
        self.open_orders = dict()    # 已发单的信息

        # 检查网格参数是否正常更新
        self.grid_lines = dict()    # 网格参数

    def update_parameters(self,
        order_qty: Optional[float]=None,
        available_money: Optional[float]=None,
        order_price: Optional[float]=None,
        direction: Optional[int]=None,
        limit_price: Optional[float]=None,
        open_orders: Optional[Dict]=None,
    ) -> None:
        """更新参数"""
        if order_qty is not None:
            self.order_qty = order_qty
        if available_money is not None:
            self.available_money = available_money
        if order_price is not None:
            self.order_price = order_price
        if direction is not None:
            self.direction = direction
        if limit_price is not None:
            self.limit_price = limit_price
        if open_orders is not None:
            self.open_orders = open_orders

    def quit(self, msg) -> None:
        """风控不通过报错"""
        raise ValueError(msg)

    def check(self,
        order_qty: Optional[float]=None,
        available_money: Optional[float]=None,
        order_price: Optional[float]=None,
        direction: Optional[int]=None,
        limit_price: Optional[float]=None,
        open_orders: Optional[Dict]=None,
    ) -> bool:
        """风控主函数"""
        # 更新参数
        self.update_parameters(
            order_qty = order_qty,
            available_money = available_money,
            order_price = order_price,
            direction = direction,
            limit_price = limit_price,
            open_orders = open_orders,
        )

        # 风控检查
        msg = ''
        if not self.check_order_qty():
            msg += f"触发风控！下单手数超过限制：{self.order_qty}"
        if not self.check_available_money():
            msg += f"触发风控！资金不足：{self.available_money}"
        if not self.check_double_send():
            msg += f"触发风控！该网格重复发单：{self.order_price}"
        if msg != '':
            # self.quit(msg)
            return False
        
        if not self.check_order_price():
            msg += f"触发风控！该网格超过网格边际：{self.order_price}"
            return False
        return True

    def check_order_qty(self) -> bool:
        """检查下单手数是否合规"""
        if (self.order_qty > 30) | (self.order_qty < 0):
            return False
        return True

    def check_order_price(self) -> bool:
        """检查下单价格是否超过网格边际"""
        if (self.direction == DIRECTION_LONG) & (self.order_price < self.limit_price):
            return False
        if (self.direction == DIRECTION_SHORT) & (self.order_price > self.limit_price):
            return False
        return True

    def check_available_money(self) -> bool:
        """检查可用资金是否充足"""
        if self.available_money < 100000:
            return False
        return True

    def check_double_send(self) -> bool:
        """检查是否重复发单"""
        for _, v in self.open_orders.items():
            if round(float(v.order_price), 2) == round(float(self.order_price), 2):
                if (v.status != STATUS_ALLTRADED) | (v.status != STATUS_CANCELLED) | (v.status != STATUS_PARTTRADED_PARTCANCELLED):
                    # 该订单如果全部成交，或该订单已撤单，或该订单部成部撤，则没问题
                    continue
                else:
                    # 否者，该网格线上有挂单未成交
                    return False
        return True

    def check_gridlines(self) -> bool:
        """检查网格参数是否正确更新了"""
        pass