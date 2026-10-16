function total(items) { return items.reduce((sum, n) => sum + n, 0); }
if (typeof module !== 'undefined') module.exports = { total };
