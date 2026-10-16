function total(items) { return items.reduce((s, x) => s + x, 0); }
if (typeof module !== 'undefined') module.exports = { total };
