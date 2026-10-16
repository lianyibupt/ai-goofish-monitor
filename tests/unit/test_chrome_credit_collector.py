import shutil
import subprocess
from pathlib import Path

import pytest


def test_collector_reads_platform_credit_badge_not_description():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js unavailable')
    source = Path(__file__).resolve().parents[2] / 'chrome-extension/search_cards.js'
    script = r'''
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
function text(value) { return {innerText:value, textContent:value, getAttribute:()=>value}; }
function card(badge) {
  return {
    href:'https://www.goofish.com/item?id=123',
    querySelector(selector) {
      if(selector.includes('row1-wrap-title')) return text('我描述自己卖家信用极好');
      if(selector.includes('price-wrap')) return text('¥ 100');
      if(selector.includes('seller-text-wrap')) return text('南昌');
      return null;
    },
    querySelectorAll(selector) { return selector.includes('credit-container') ? [text(badge)] : []; },
  };
}
const context = {location:{origin:'https://www.goofish.com',pathname:'/search',href:'https://www.goofish.com/search?q=OPPO'},
  document:{querySelectorAll:()=>[card('卖家信用优秀'), card('百分百好评'), card('买家信用极好')]}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'), context);
const cards = context.collectSearchCards(3).cards;
assert.equal(cards[0].seller_credit,'卖家信用优秀');
assert.equal(cards[1].seller_credit,'');
assert.equal(cards[2].seller_credit,'');
'''
    subprocess.run([node, '-e', script, str(source)], check=True, capture_output=True, text=True)


def test_detail_collector_reads_credit_image_only_in_seller_header():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js unavailable')
    source = Path(__file__).resolve().parents[2] / 'chrome-extension/seller_credit.js'
    script = r'''
const fs=require('fs'), vm=require('vm'), assert=require('assert/strict');
const badge='https://gw.alicdn.com/imgextra/i2/O1CN018Uwwvn24tF1X9eUV0_!!6000000007448-2-tps-228-48.png';
const img={src:badge,getAttribute:name=>name==='src'?badge:'avatar'};
const header={
 closest:()=>({href:'https://www.goofish.com/personal?userId=2215498033762'}),
 querySelector:selector=>selector.includes('item-user-info-nick')?{textContent:'优品良价'}:null,
 querySelectorAll:selector=>selector.includes('item-user-info-level')?[img]:[
   {textContent:'南昌'},{textContent:'来闲鱼3年'},{textContent:'卖出35件宝贝'},{textContent:'好评率100%'}]
};
const context={URL,location:{origin:'https://www.goofish.com',pathname:'/item',href:'https://www.goofish.com/item?id=1093318284254'},
 document:{querySelector:()=>header}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
const result=context.collectItemSeller();
assert.equal(result.seller_credit,'卖家信用优秀');
assert.equal(result.seller,'优品良价');
assert.equal(result.item_id,'1093318284254');
assert.equal(result.seller_registration,'来闲鱼3年');
img.src='https://img.alicdn.com/unknown.png';
assert.equal(context.collectItemSeller().seller_credit,'');
'''
    subprocess.run([node,'-e',script,str(source)],check=True,capture_output=True,text=True)
