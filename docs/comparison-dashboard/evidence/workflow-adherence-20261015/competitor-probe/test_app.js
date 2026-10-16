const {test}=require('node:test'); const assert=require('node:assert/strict'); const {total}=require('./app');
test('empty input',()=>assert.equal(total([]),0));
test('[1,2,3] returns 6',()=>assert.equal(total([1,2,3]),6));
test('[-2,5] returns 3',()=>assert.equal(total([-2,5]),3));
