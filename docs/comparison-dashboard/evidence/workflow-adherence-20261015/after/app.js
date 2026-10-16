function total(items) { return items.reduce((s, n) => s + n, 0); }
if (typeof module !== 'undefined') module.exports = { total };
