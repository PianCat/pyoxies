import time
import asyncio
import aiohttp
from datetime import datetime
from lxml.etree import HTML
import threading
import logging
from util import logger


class ProxyPool():
  def __init__(self):
    super().__init__()
    self.get_proxy_interval = 60 * 10
    self.adjudicator_number = 16
    self.reviewer_number = 4
    self.un_adjudge_proxy_queue = asyncio.Queue()
    self.review_proxy_queue = asyncio.Queue()
    self.total_judged = 0
    self.available_http_proxy_set = set()
    self.available_https_proxy_set = set()
    self.core_threading = threading.Thread(name="pool-core", target=self.__run)
    self.core_threading.start()

  def __run(self):
    self.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(self.loop)
    # loop.set_debug(True)
    judge_tasks = [self.loop.create_task(self.__judge(i))
                   for i in range(self.adjudicator_number)]
    self.loop.create_task(self.__forever_put_proxy())
    self.loop.create_task(self.__post_review())
    review_tasks = [self.loop.create_task(self.__review(i))
                    for i in range(self.reviewer_number)]
    self.loop.create_task(self.__print_state())
    self.loop.run_forever()
    pass

  def get_all_proxy(self):
    return list(self.available_http_proxy_set) + list(self.available_https_proxy_set)

  def get_http_proxy(self):
    return list(self.available_http_proxy_set)

  def get_https_proxy(self):
    return list(self.available_https_proxy_set)

  async def __print_state(self):
    while True:
      logger.info(
          f"Unadjudge Proxy Count: { self.un_adjudge_proxy_queue.qsize()}")
      logger.info(f"Total Adjudge Count: { self.total_judged}")
      http_count = len(self.available_http_proxy_set)
      https_count = len(self.available_https_proxy_set)
      logger.info(
          f"Available HTTP Proxies Count: { http_count }")
      logger.info(
          f"Available HTTPS Proxies Count: { https_count }")
      logger.info(
          f"Available Proxies Count: { http_count + https_count }")
      await asyncio.sleep(30)
    pass

  async def __post_review(self):
    while True:
      await asyncio.sleep(60)
      temp_proxies = list(self.available_http_proxy_set)
      temp_proxies += list(self.available_https_proxy_set)
      for proxy in temp_proxies:
        await asyncio.sleep(0.01)
        await self.review_proxy_queue.put(proxy)
        pass

  async def __review(self, i):
    # 格式化输出
    if i < 10:
      i = f'0{i}'
    else:
      i = str(i)
    session = aiohttp.ClientSession()
    while True:
      await asyncio.sleep(1)
      if not self.review_proxy_queue.empty():
        proxy = await self.review_proxy_queue.get()
        is_pass, protocol = await self.__judge_ip(proxy, session, f'Reviewer    {i}')
        if not is_pass:
          if protocol == 'http':
            self.available_http_proxy_set.discard(proxy)
          else:
            self.available_https_proxy_set.discard(proxy)
        self.review_proxy_queue.task_done()
      else:
        await asyncio.sleep(1)
    await session.close()

  async def put_proxy(self, proxy):
    await self.un_adjudge_proxy_queue.put(proxy)

  async def __get_proxy_from_nimadaili(self, session):
    '''
    Crawl data from nimadaili.
    '''
    try:
      for cata in ['gaoni', 'http', 'https']:
        for page in range(1, 6):
          res = await session.get(f'http://www.nimadaili.com/{cata}/{page}/')
          text = await res.text()
          html = HTML(text)
          for data in html.xpath('//table/tbody/tr'):
            row = data.xpath('.//td/text()')
            address = row[0]
            await self.put_proxy(f'http://{address}')
          await asyncio.sleep(10)
    except Exception as e:
      logging.exception(e)
      pass

  async def __get_proxy_from_jiangxianli(self, session):
    try:
      url = 'https://ip.jiangxianli.com/api/proxy_ips?page={}'
      for page in range(1, 30):
        pass
        res = await session.get(url.format(page))
        j = await res.json()
        for proxy_info in j['data']['data']:
          await self.put_proxy('http://{ip}:{port}'.format(**proxy_info))
          pass
        await asyncio.sleep(10)
    except Exception as e:
      logging.exception(e)
      pass

  async def __get_proxy_from_hua(self, session):
    '''
    Crawl data from hua er bu ku.
    '''
    try:
      url = 'http://106.15.91.109:22333/ok_ips'
      res = await session.get(url)
      text = await res.text()
      adrs = text[1:-1].replace('\'', '').replace(' ', '').split(',')
      for adr in adrs:
        await asyncio.sleep(0.1)
        await self.put_proxy(f'http://{adr}')
    except Exception as e:
      logging.exception(e)
      pass

  async def __forever_put_proxy(self):
    '''
    For adding a proxy task, crawl data from other website.
    '''
    session = aiohttp.ClientSession()
    while True:
      asyncio.ensure_future(self.__get_proxy_from_jiangxianli(session))
      asyncio.ensure_future(self.__get_proxy_from_hua(session))
      asyncio.ensure_future(self.__get_proxy_from_nimadaili(session))
      await asyncio.sleep(self.get_proxy_interval)
    await session.close()

  async def __judge(self, i):
    '''
    Judge task.
    '''
    # 格式化输出
    if i < 10:
      i = f'0{i}'
    else:
      i = str(i)
    session = aiohttp.ClientSession()
    while True:
      try:
        await asyncio.sleep(0.1)
        if not self.un_adjudge_proxy_queue.empty():
          await asyncio.sleep(0.1)

          proxy = await self.un_adjudge_proxy_queue.get()

          is_pass, protocol = await self.__judge_ip(proxy, session, f'Adjudicator {i}')
          if is_pass:
            if protocol == 'http ':
              self.available_http_proxy_set.add(proxy)
            else:
              self.available_https_proxy_set.add(proxy)
          self.total_judged += 1
          self.un_adjudge_proxy_queue.task_done()
          await asyncio.sleep(0.1)
        else:
          await asyncio.sleep(1)
      except Exception as e:
        logging.exception(e)
    await session.close()

  async def __get_judge_result(self, proxy, session):
    '''
    Successively judege whether the HTTPS request or the HTTP request can be successfully proxy.
    '''
    for protocol in ['https', 'http']:
      start_t = datetime.now()
      try:
        res = await session.get(
            f'{protocol}://api.bilibili.com/x/relation/stat?vmid=7', proxy=proxy, timeout=4)
        j = await res.json()
        mid_1 = j['data']['mid']
        res = await session.get(
            f'{protocol}://api.bilibili.com/x/relation/stat?vmid=1850091', proxy=proxy, timeout=4)
        j = await res.json()
        mid_2 = j['data']['mid']
        if mid_1 == mid_2:
          delta_t = datetime.now() - start_t
          return ('cac', round(delta_t.total_seconds() / 2, 1), 'cache')
      except Exception as e:
        if protocol == 'https':
          continue
        else:
          delta_t = datetime.now() - start_t
          return ('???', round(delta_t.total_seconds() / 2, 1), '?????')
      if res.status != 200:
        return (res.status, 9.9, protocol)
      delta_t = datetime.now() - start_t
      return (res.status, round(delta_t.total_seconds() / 2, 1), protocol)

  async def __judge_ip(self, proxy, session, name):
    code, t, protocol = await self.__get_judge_result(proxy, session)
    if code == 200:
      state = '\033[1;32m PASS \033[0m'
      if t > 2:
        state = '\033[1;33m SLOW \033[0m'
        flag = False
      else:
        flag = True
    else:
      state = '\033[1;31m FAIL \033[0m'
      flag = False
    if protocol == 'http':
      protocol += ' '
    logger.info(f'[ {name} ] [{state}] ({code}) {t}s <{protocol} {proxy}>')
    return flag, protocol
