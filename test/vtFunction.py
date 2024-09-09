# encoding: UTF-8

"""
包含一些开放中常用的函数
"""

import decimal
import json
import datetime

MAX_NUMBER = 10000000000000
MAX_DECIMAL = 4

#----------------------------------------------------------------------
def loadJson(filePath):
    """读取json文件，返回字典列表"""
    try:
        f = file(filePath)
        l = json.load(f)
        return l
    except:
        print(u'文件 : '+str(filePath)+u' 不存在')
        
#----------------------------------------------------------------------
def writeJson(dictList,dictFile):
    """把json数据写入到文件"""
    dict_str = json.dumps(dictList,ensure_ascii=False)
    dictFile = open(self.name+".json", 'w')
    dictFile.write(dict_str)
    dictFile.close( )
        

#----------------------------------------------------------------------
def todayDate():
    """获取当前本机电脑时间的日期"""
    return datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)    

 
