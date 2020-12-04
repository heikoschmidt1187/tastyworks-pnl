#!/usr/bin/python3
#
# Copyright (C) 2020 Florian La Roche <Florian.LaRoche@gmail.com>
#
# Generate data for a German tax income statement from Tastyworks trade history.
#
# Download your trade history as csv file from
# https://trade.tastyworks.com/index.html#/transactionHistoryPage
# (Choose "Activity" and then "History" and then setup the filter for a
# custom period of time and download it as csv file.)
# Newest entries in the csv file should be on the top and it should contain the complete
# history over all years. The csv file has the following first line:
# Date/Time,Transaction Code,Transaction Subcode,Symbol,Buy/Sell,Open/Close,Quantity,Expiration Date,Strike,Call/Put,Price,Fees,Amount,Description,Account Reference
#
# sudo apt-get install python3-pandas
#
# TODO:
# - Profit and loss is only calculated like for normal stocks,
#   no special handling for options until now.
# - Missing conversion from USD to EUR.
#   - Download official conversion data and include it also inline here.
# - Filter out tax gains due to currency changes.
# - Does not work with futures.
# - Translate text output into German.
# - Complete the list of non-stocks.
# - Add test data for users to try out.
# - Output yearly information, currently only the end result is printed once.
# - Break up report into: dividends, withholding-tax, interest, fees, stocks, other.
# - Check if dates are truely ascending.
# - Improve output of open positions.
# - Use pandas.isna(x)?
#

import sys
import getopt
import pandas
import math
from collections import deque

def check_tcode(tcode, tsubcode, description):
    if tcode not in ['Money Movement', 'Trade', 'Receive Deliver']:
        raise
    if tcode == 'Money Movement':
        if tsubcode not in ['Transfer', 'Deposit', 'Credit Interest', 'Balance Adjustment', 'Fee', 'Withdrawal', 'Dividend']:
            raise
        if tsubcode == 'Balance Adjustment' and description != 'Regulatory fee adjustment':
            raise
    elif tcode == 'Trade':
        if tsubcode not in ['Sell to Open', 'Buy to Close', 'Buy to Open', 'Sell to Close']:
            raise
    elif tcode == 'Receive Deliver':
        if tsubcode not in ['Sell to Open', 'Buy to Close', 'Buy to Open', 'Sell to Close', 'Expiration', 'Assignment', 'Exercise']:
            raise

def check_param(buysell, openclose, callput):
    if str(buysell) not in ['nan', 'Buy', 'Sell']:
        raise
    if str(openclose) not in ['nan', 'Open', 'Close']:
        raise
    if str(callput) not in ['nan', 'C', 'P']:
        raise

def check_trade(tsubcode, check_amount, amount):
    #print('FEHLER:', check_amount, amount)
    if tsubcode not in ['Expiration', 'Assignment', 'Exercise']:
        if not math.isclose(check_amount, amount, abs_tol=0.00001):
            raise
    else:
        if str(amount) != 'nan' and amount != .0:
            raise
        if str(check_amount) != 'nan' and check_amount != .0:
            raise

# Is the symbol a real stock or anything else
# like an ETF or fond?
def is_stock(symbol, conservative=True):
    # Well known ETFs:
    if symbol in ['DXJ','EEM','EFA','EFA','EWZ','FEZ','FXB','FXE','FXI',
        'GDX','GDXJ','GLD','HYG','IEF','IWM','IYR','KRE','OIH','QQQ',
        'RSX','SLV','SMH','SPY','TLT','UNG','USO','VXX','XBI','XHB','XLB',
        'XLE','XLF','XLI','XLK','XLP','XLU','XLV','XME','XOP','XRT']:
        return False
    # Well known stock names:
    if symbol in ['M','AAPL','TSLA']:
        return True
    # The conservative way is to through an exception if we are not sure.
    # Change the default of the function if you are in a hurry to get things running.
    if conservative:
        print('No idea if this is a stock:', symbol)
        raise
    return True # Just assume this is a normal stock if not in the above list

def sign(x):
    if x >= 0:
        return 1
    return -1

def fifo_add(fifos, quantity, price, asset, debug=False):
    if debug:
        print_fifos()
        print('fifo_add', quantity, price, asset)
    # Detect if this is an option we are working with as
    # we have to pay taxes for selling an option:
    # This is a gross hack, should we check the "expire" param?
    #is_option = (len(asset) > 10)
    pnl = .0
    #if is_option and quantity < 0:
    #    pnl = quantity * price
    if fifos.get(asset) == None:
        fifos[asset] = deque()
    fifo = fifos[asset]
    while len(fifo) > 0:
        if sign(fifo[0][1]) == sign(quantity):
            break
        if abs(fifo[0][1]) >= abs(quantity):
            pnl += quantity * (fifo[0][0] - price)
            fifo[0][1] += quantity
            if fifo[0][1] == 0:
                fifo.popleft()
                if len(fifo) == 0:
                    del fifos[asset]
            return pnl
        else:
            pnl += fifo[0][1] * (price - fifo[0][0])
            quantity += fifo[0][1]
            fifo.popleft()
    fifo.append([price, quantity])
    return pnl

def fifos_islong(fifos, asset):
    return fifos[asset][0][1] > 0

def print_fifos(fifos):
    print('open positions:')
    for fifo in fifos:
        print(fifo)

def check(wk, year):
    #print(wk)
    fifos = {}
    total_fees = .0           # sum of all fees paid
    total = .0                # account total
    interest_paid = .0
    interest_recv = .0
    fee_adjustments = .0
    dividends = .0
    withholding_tax = .0      # withholding tax = German 'Quellensteuer'
    withdrawal = .0
    pnl_stocks = .0
    pnl = .0
    cur_year = None
    check_account_ref = None
    for i in range(len(wk) - 1, -1, -1):
        datetime = wk['Date/Time'][i]
        if cur_year != str(datetime)[:4]:
            if cur_year is not None:
                pass # Print out old yearly data and reset counters
            cur_year = str(datetime)[:4]
        tcode = wk['Transaction Code'][i]
        tsubcode = wk['Transaction Subcode'][i]
        description = wk['Description'][i]
        check_tcode(tcode, tsubcode, description)
        buysell = wk['Buy/Sell'][i]
        openclose = wk['Open/Close'][i]
        callput = wk['Call/Put'][i]
        check_param(buysell, openclose, callput)
        account_ref = wk['Account Reference'][i]
        if check_account_ref is None:
            check_account_ref = account_ref
        if account_ref != check_account_ref: # check if this does not change over time
            raise
        fees = float(wk['Fees'][i])
        total_fees += fees
        amount = float(wk['Amount'][i])
        total += amount - fees

        quantity = wk['Quantity'][i]
        if str(quantity) != 'nan':
            if int(quantity) != quantity:
                raise
            quantity = int(quantity)
        symbol = wk['Symbol'][i]
        expire = wk['Expiration Date'][i]
        strike = wk['Strike'][i]
        price = wk['Price'][i]
        if str(price) == 'nan':
            price = .0
        if price < .0:
            raise

        if tcode == 'Money Movement':
            if tsubcode == 'Transfer':
                print(datetime, f'{amount:10.2f}', '$ transferred:', description)
            elif tsubcode  in ['Deposit', 'Credit Interest']:
                if description == 'INTEREST ON CREDIT BALANCE':
                    print(datetime, f'{amount:10.2f}', '$ interest')
                    if amount > .0:
                        interest_recv += amount
                    else:
                        interest_paid += amount
                else:
                    if amount > .0:
                        dividends += amount
                        print(datetime, f'{amount:10.2f}', '$ dividends: %s,' % symbol, description)
                    else:
                        withholding_tax += amount
                        print(datetime, f'{amount:10.2f}', '$ withholding tax: %s,' % symbol, description)
                if fees != .0:
                    raise
            elif tsubcode == 'Balance Adjustment':
                fee_adjustments += amount
                total_fees += amount
                if fees != .0:
                    raise
            elif tsubcode == 'Fee':
                # XXX Additional fees for dividends paid in short stock? Interest fees?
                print(datetime, f'{amount:10.2f}', '$ fees: %s,' % symbol, description)
                fee_adjustments += amount
                total_fees += amount
                if amount >= .0:
                    raise
                if fees != .0:
                    raise
            elif tsubcode == 'Withdrawal':
                # XXX In my case dividends paid for short stock:
                print(datetime, f'{amount:10.2f}', '$ dividends paid: %s,' % symbol, description)
                withdrawal += amount
                if amount >= .0:
                    raise
                if fees != .0:
                    raise
            elif tsubcode == 'Dividend':
                if amount > .0:
                    dividends += amount
                    print(datetime, f'{amount:10.2f}', '$ dividends: %s,' % symbol, description)
                else:
                    withholding_tax += amount
                    print(datetime, f'{amount:10.2f}', '$ withholding tax: %s,' % symbol, description)
                if fees != .0:
                    raise
        else:
            asset = symbol
            if str(expire) != 'nan':
                from datetime import datetime as pydatetime
                expire = pydatetime.strptime(expire, '%m/%d/%Y').strftime('%y-%m-%d')
                price *= 100.0
                if int(strike) == strike:
                    strike = int(strike)
                asset = '%s %s%s %s' % (symbol, callput, strike, expire)
                check_stock = False
            else:
                check_stock = is_stock(symbol)
            # 'buysell' is not set correctly for 'Expiration'/'Exercise'/'Assignment' entries,
            # so we look into existing positions to check if we are long or short (we cannot
            # be both, so this test should be safe):
            if str(buysell) == 'Sell' or \
                (tsubcode in ['Expiration', 'Exercise', 'Assignment'] and fifos_islong(fifos, asset)):
                quantity = - quantity
            check_trade(tsubcode, - (quantity * price), amount)
            price = abs((amount - fees) / quantity)
            local_pnl = fifo_add(fifos, quantity, price, asset)
            print(datetime, f'{local_pnl:10.2f}', '$', f'{amount-fees:10.2f}', '$', '%5d' % quantity, asset)
            if check_stock:
                pnl_stocks += local_pnl
            else:
                pnl += local_pnl

    wk.drop('Account Reference', axis=1, inplace=True)

    print()
    print('Total sums paid and received:')
    print('dividends received:   ', f'{dividends:10.2f}', '$')
    print('withholding tax paid: ', f'{-withholding_tax:10.2f}', '$')
    if withdrawal != .0:
        print('dividends paid:       ', f'{-withdrawal:10.2f}', '$')
    print('interest received:    ', f'{interest_recv:10.2f}', '$')
    if interest_paid != .0:
        print('interest paid:        ', f'{-interest_paid:10.2f}', '$')
    print('fee adjustments:      ', f'{fee_adjustments:10.2f}', '$')
    print('pnl stocks:           ', f'{pnl_stocks:10.2f}', '$')
    print('pnl other:            ', f'{pnl:10.2f}', '$')
    print()
    print('New end sums and open positions:')
    print('total fees paid:      ', f'{total_fees:10.2f}', '$')
    print('account end total:    ', f'{total:10.2f}', '$')
    print_fifos(fifos)

    #print(wk)

def help():
    print('tw-pnl.py *.csv')

def main(argv):
    year = None
    try:
        opts, args = getopt.getopt(argv, 'hy:', ['help', 'year'])
    except getopt.GetoptError:
        help()
        sys.exit(2)
    for opt, arg in opts:
        if opt in ('-h', '--help'):
            help()
            sys.exit()
        elif opt in ('-y', '--year'):
            year = arg
    args.reverse()
    for csv_file in args:
        wk = pandas.read_csv(csv_file, parse_dates=['Date/Time']) # 'Expiration Date'])
        check(wk, year)

if __name__ == '__main__':
    main(sys.argv[1:])
