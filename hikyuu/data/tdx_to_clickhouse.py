# coding:utf-8
#
# The MIT License (MIT)
#
# Copyright (c) 2010-2019 fasiondog/hikyuu
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import os.path
import struct
import datetime
import math
import sys

import mysql.connector
import tables as tb

from io import SEEK_END, SEEK_SET

from hikyuu import Datetime
from hikyuu.data.common import get_stktype_list, MARKET
from hikyuu.data.common_clickhouse import (
    create_database, get_codepre_list, get_table, get_lastdatetime, update_extern_data
)


def ProgressBar(cur, total):
    percent = '{:.0%}'.format(cur / total)
    sys.stdout.write('\r')
    sys.stdout.write("[%-50s] %s" % ('=' * int(math.floor(cur * 50 / total)), percent))
    sys.stdout.flush()


def tdx_import_stock_name_from_file(connect, filename, market, quotations=None):
    """更新每只股票的名称、当前是否有效性、起始日期及结束日期
        如果导入的代码表中不存在对应的代码，则认为该股已失效

        :param connect: mysql实例
        :param filename: 代码表文件名
        :param market: 'SH' | 'SZ'
        :param quotations: 待导入的行情类别列表，空为导入全部 'stock' | 'fund' | 'bond' | None
    """
    newStockDict = {}
    with open(filename, 'rb') as f:
        data = f.read(50)
        data = f.read(314)
        while data:
            a = struct.unpack('6s 17s 8s 283s', data)
            stockcode = a[0].decode()
            try:
                stockname = a[2].decode(encoding='gbk').encode('utf8')
            except:
                print(stockname)
                data = f.read(314)
                continue
            pos = stockname.find(0x00)
            if pos >= 0:
                stockname = stockname[:pos]
            newStockDict[stockcode] = stockname.decode(encoding='utf8').strip()
            data = f.read(314)

    stktype_list = get_stktype_list(quotations)
    sql = f"select code, name, type, valid, startDate, endDate from hku_base.stock where market='{market}' and type in {stktype_list}"
    a = connect.query(sql)
    a = a.result_rows
    oldStockDict = {}
    buf = {}
    for oldstock in a:
        oldcode, oldname, oldtype, oldvalid, oldstartDate, oldendDate = oldstock[0], oldstock[1], oldstock[2], int(
            oldstock[3], oldstock[4], oldstock[5])
        oldmarketcode = f'{market}{oldcode}'
        oldStockDict[oldcode] = oldmarketcode

        # 新的代码表中无此股票，则置为无效
        if (oldvalid == 1) and (oldcode not in newStockDict):
            connect.command(f"delete from hku_base.stock where market='{market}' and code='{oldcode}'")
            buf.append((market, oldcode, oldname, oldtype,  0, today, 99999999))

        # 股票名称发生变化，更新股票名称;如果原无效，则置为有效
        if oldcode in newStockDict:
            if oldname != newStockDict[oldcode]:
                connect.command(f"delete from hku_base.stock where market='{market}' and code='{oldcode}'")
                buf.append((market, oldcode, newStockDict[oldcode], oldtype,
                           oldtype, oldvalid, oldstartDate, oldendDate))
            if oldvalid == 0:
                connect.command(f"delete from hku_base.stock where market='{market}' and code='{oldcode}'")
                buf.append((market, oldcode, oldname, oldtype, 1, oldstartDate, 99999999))

    # 处理新出现的股票
    codepre_list = get_codepre_list(connect, market, quotations)

    today = datetime.date.today()
    today = today.year * 10000 + today.month * 100 + today.day
    count = 0
    for code in newStockDict:
        if code not in oldStockDict:
            for codepre in codepre_list:
                length = len(codepre[0])
                if code[:length] == codepre[0]:
                    count += 1
                    buf.append((market, code, newStockDict[code], codepre[1], 1, today, 99999999))
                    break

    # print('%s新增股票数：%i' % (market.upper(), count))
    if len(buf) > 0:
        ic = connect.create_insert_context(table='stock', database='hku_base',
                                           column_names=['market', 'code', 'name',
                                                         'type', 'valid', 'startDate', 'endDate'],
                                           data=buf)
        connect.insert(context=ic)
    return count


def to_clickhouse_ktype(ktype):
    n_ktype = ktype.upper()
    if n_ktype == 'DAY':
        return n_ktype
    if n_ktype == '1MIN':
        return 'MIN'
    if n_ktype == '5MIN':
        return 'MIN5'


def update_stock_info(connect, market, code):
    sql = f"select toUInt32(min(date)), toUInt32(max(date)) from hku_data.day_k where market='{market}' and code='{code}'"
    a = connect.query(sql)
    a = a.result_rows
    if a:
        sql = f"select valid, startDate, endDate from hku_base.stock where market='{market}' and code='{code}'"
        b = connect.query(sql)
        b = b.result_rows
        if b:
            valid, startDate, endDate = b[0]
            ticks = 1000000
            now_start = Datetime.from_timestamp_utc(a[0][0]*ticks).ymd
            now_end = Datetime.from_timestamp_utc(a[0][1]*ticks).ymd
            if valid == 1 and now_start != startDate:
                sql = f"alter table hku_base.stock update startDate={now_start}, endDate=99999999 where market='{market}' and code='{code}'"
                connect.command(sql)
            elif valid == 0 and now_end != endDate:
                sql = f"alter table hku_base.stock update startDate={now_start}, endDate={now_end} where market='{market}' and code='{code}'"
                connect.command(sql)

            if ((code == "000001" and market == MARKET.SH)
                or (code == "399001" and market == MARKET.SZ)
                    or (code == "830799" and market == MARKET.BJ)):
                sql = f"alter table hku_base.market update lastDate={now_end} where market='{market}'"
                connect.command(sql)


def tdx_import_day_data_from_file(connect, filename, ktype, market, stock_record):
    """从通达信盘后数据导入日K线

    :param connect : 数据库连接实例
    :param filename: 通达信日线数据文件名
    :param ktype  :  'DAY' | '1MIN' | '5MIN'
    :param stock_record: 股票的相关数据 (stockid, marketid, code, valid, type)
    :return: 导入的记录数
    """
    add_record_count = 0
    if not os.path.exists(filename):
        return add_record_count

    # market, code, name, type, valid, startDate, endDate
    code, stktype = stock_record[1], stock_record[3]

    table = get_table(connect, market, code, ktype)
    lastdatetime = get_lastdatetime(connect, table)
    if lastdatetime is not None:
        lastdatetime = lastdatetime.ymd

    buf = []
    with open(filename, 'rb') as src_file:
        data = src_file.read(32)
        while data:
            record = struct.unpack('iiiiifii', data)
            if lastdatetime and record[0] <= lastdatetime:
                data = src_file.read(32)
                continue

            if record[2] >= record[1] >= record[3] > 0 \
                    and record[2] >= record[4] >= record[3] > 0 \
                    and record[5] >= 0 \
                    and record[6] >= 0:
                buf.append(
                    (
                        market, code,
                        record[0] * 10000, record[1] * 0.01, record[2] * 0.01, record[3] * 0.01,
                        record[4] * 0.01, round(record[5] * 0.001),
                        record[6] if stktype == 2 else round(record[6] * 0.01)
                    )
                )
                add_record_count += 1
            data = src_file.read(32)

    if len(buf) > 0:
        ic = connect.create_insert_context(table=table[0], data=buf)
        connect.insert(context=ic)
    return add_record_count


def tdx_import_min_data_from_file(connect, filename, ktype, market, stock_record):
    """从通达信盘后数据导入1分钟或5分钟K线

    :param connect : sqlite3连接实例
    :param filename: 通达信K线数据文件名
    :param ktype: 'DAY' | '1MIN' | '5MIN'
    :param stock_record: 股票的相关数据 (stockid, marketid, code, valid, type)
    :return: 导入的记录数
    """
    add_record_count = 0
    if not os.path.exists(filename):
        return add_record_count

    # market, code, name, type, valid, startDate, endDate
    code, stktype = stock_record[1], stock_record[3]

    table = get_table(connect, market, code, to_clickhouse_ktype(ktype))
    lastdatetime = get_lastdatetime(connect, table)
    if lastdatetime is not None:
        lastdatetime = lastdatetime.ymdhm

    buf = []
    with open(filename, 'rb') as src_file:

        def trans_date(yymm, hhmm):
            tmp_date = yymm >> 11
            remainder = yymm & 0x7ff
            year = tmp_date + 2004
            month = remainder // 100
            day = remainder % 100
            hh = hhmm // 60
            mm = hhmm % 60
            return year * 100000000 + month * 1000000 + day * 10000 + hh * 100 + mm

        def get_date(pos):
            src_file.seek(pos * 32, SEEK_SET)
            data = src_file.read(4)
            a = struct.unpack('HH', data)
            return trans_date(a[0], a[1])

        def find_pos():
            src_file.seek(0, SEEK_END)
            pos = src_file.tell()
            total = pos // 32
            if lastdatetime is None:
                return total, 0

            low, high = 0, total - 1
            mid = high // 2
            while mid <= high:
                cur_date = get_date(low)
                if cur_date > lastdatetime:
                    mid = low
                    break

                cur_date = get_date(high)
                if cur_date <= lastdatetime:
                    mid = high + 1
                    break

                cur_date = get_date(mid)
                if cur_date <= lastdatetime:
                    low = mid + 1
                else:
                    high = mid - 1

                mid = (low + high) // 2

            return total, mid

        file_total, pos = find_pos()
        if pos < file_total:
            src_file.seek(pos * 32, SEEK_SET)

            data = src_file.read(32)
            while data:
                record = struct.unpack('HHfffffii', data)
                if record[3] >= record[2] >= record[4] > 0\
                        and record[3] >= record[5] >= record[4] > 0\
                        and record[5] >= 0 \
                        and record[6] >= 0:
                    buf.append(market, code, trans_date(record[0], record[1]), round(record[2], 2),
                               round(record[3], 2), round(record[4], 2), round(record[5],
                                                                               2), record[6] * 0.001,
                               record[7] if stktype == 2 else round(record[7] * 0.01))
                    add_record_count += 1

                data = src_file.read(32)

    if len(buf) > 0:
        ic = connect.create_insert_context(table=table[0], data=buf)
        connect.insert(context=ic)
    return add_record_count


def tdx_import_data(connect, market, ktype, quotations, src_dir, progress=ProgressBar):
    """导入通达信指定盘后数据路径中的K线数据。注：只导入基础信息数据库中存在的股票。

    :param connect   : sqlit3链接
    :param market    : 'SH' | 'SZ'
    :param ktype     : 'DAY' | '1MIN' | '5MIN'
    :param quotations: 'stock' | 'fund' | 'bond'
    :param src_dir   : 盘后K线数据路径，如上证5分钟线：D:\\Tdx\\vipdoc\\sh\\fzline
    :param progress  : 进度显示函数
    :return: 导入记录数
    """
    add_record_count = 0
    market = market.upper()

    if ktype.upper() == "DAY":
        suffix = ".day"
        func_import_from_file = tdx_import_day_data_from_file
    elif ktype.upper() == "1MIN":
        suffix = ".lc1"
        func_import_from_file = tdx_import_min_data_from_file
    elif ktype.upper() == "5MIN":
        suffix = ".lc5"
        func_import_from_file = tdx_import_min_data_from_file

    stktype_list = get_stktype_list(quotations)
    sql = f"select market, code, name, type, valid, startDate, endDate from hku_base.stock where market='{market}' and type in {stktype_list}"
    a = connect.query(sql)
    a = a.result_rows

    total = len(a)
    for i, stock in enumerate(a):
        if stock[4] == 0:
            if progress:
                progress(i, total)
            continue

        filename = src_dir + "\\" + market.lower() + stock[1] + suffix
        this_count = func_import_from_file(connect, filename, ktype, market, stock)
        add_record_count += this_count
        if this_count > 0:
            if ktype == 'DAY':
                stock[1]
                update_stock_info(connect, market.upper(), stock[1])
                update_extern_data(connect, market.upper(), stock[1], "DAY")
            elif ktype == '5MIN':
                update_extern_data(connect, market.upper(), stock[1], "5MIN")
        if progress:
            progress(i, total)

    if ktype == "DAY":
        connect.command('OPTIMIZE TABLE hku_data.day_k FINAL')
    return add_record_count


if __name__ == '__main__':

    import time
    starttime = time.time()

    host = '127.0.0.1'
    port = 3306
    usr = 'root'
    pwd = ''

    src_dir = "D:\\TdxW_HuaTai"
    quotations = ['stock', 'fund']  # 通达信盘后数据没有债券

    connect = mysql.connector.connect(user=usr, password=pwd, host=host, port=port)
    create_database(connect)

    add_count = 0

    print("导入股票代码表")
    add_count = tdx_import_stock_name_from_file(
        connect, src_dir + "\\T0002\\hq_cache\\shm.tnf", 'SH', quotations
    )
    add_count += tdx_import_stock_name_from_file(
        connect, src_dir + "\\T0002\\hq_cache\\szm.tnf", 'SZ', quotations
    )
    print("新增股票数：", add_count)

    print("\n导入上证日线数据")
    add_count = tdx_import_data(connect, 'SH', 'DAY', quotations, src_dir + "\\vipdoc\\sh\\lday")
    print("\n导入数量：", add_count)

    print("\n导入深证日线数据")
    add_count = tdx_import_data(connect, 'SZ', 'DAY', quotations, src_dir + "\\vipdoc\\sz\\lday")
    print("\n导入数量：", add_count)
    """
    print("\n导入上证5分钟数据")
    add_count = tdx_import_data(connect, 'SH', '5MIN', quotations, src_dir + "\\vipdoc\\sh\\fzline")
    print("\n导入数量：", add_count)

    print("\n导入深证5分钟数据")
    add_count = tdx_import_data(connect, 'SZ', '5MIN', quotations, src_dir + "\\vipdoc\\sz\\fzline")
    print("\n导入数量：", add_count)

    print("\n导入上证1分钟数据")
    add_count = tdx_import_data(
        connect, 'SH', '1MIN', quotations, src_dir + "\\vipdoc\\sh\\minline"
    )
    print("\n导入数量：", add_count)

    print("\n导入深证1分钟数据")
    add_count = tdx_import_data(
        connect, 'SZ', '1MIN', quotations, src_dir + "\\vipdoc\\sz\\minline"
    )
    print("\n导入数量：", add_count)
    """
    """
    print("\n导入权息数据")
    print("正在下载权息数据...")
    import urllib.request
    net_file = urllib.request.urlopen(
        'http://www.qianlong.com.cn/download/history/weight.rar', timeout=60
    )
    dest_dir = os.path.expanduser('~/.hikyuu')
    dest_filename = dest_dir + '/weight.rar'
    with open(dest_filename, 'wb') as file:
        file.write(net_file.read())

    print("下载完成，正在解压...")
    os.system('unrar x -o+ -inul {} {}'.format(dest_filename, dest_dir))

    print("解压完成，正在导入...")
    add_count = qianlong_import_weight(connect, dest_dir + '/weight', 'SH')
    add_count += qianlong_import_weight(connect, dest_dir + '/weight', 'SZ')
    print("导入数量：", add_count)
    connect.commit()
    connect.close()
    """

    endtime = time.time()
    print("\nTotal time:")
    print("%.2fs" % (endtime - starttime))
    print("%.2fm" % ((endtime - starttime) / 60))
