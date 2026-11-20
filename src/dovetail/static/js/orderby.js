// Reorder the journals of a consultation, inside each list.
//
// The lists stay apart whatever the order: they were found in different ways
// and one sorted list of all of them would state a comparison nobody made. The
// number in front of a journal is its place in the order the consultation gave
// it, and it stays with the journal when the list is reordered, so that order
// can still be read off after sorting by something else.
//
// The choice is kept in the address (?order=…), so a reload or a shared link
// shows the same thing. Without this script the lists are simply in their
// original order.
(function () {
  var select = document.getElementById("order-by");
  if (!select) return;

  // key -> [attribute, direction, kind]. Missing values always go last,
  // whichever way the rest is sorted: «unknown fee» is not the lowest fee.
  var ORDERS = {
    overall: ["overall", -1, "num"],
    topic: ["topic", -1, "num"],
    subfield: ["subfield", -1, "num"],
    field: ["field", -1, "num"],
    citations: ["citations", -1, "num"],
    references: ["references", -1, "num"],
    merit: ["merit", -1, "num"],
    fee: ["fee", 1, "num"],
    kind: ["kind", -1, "num"],
    name: ["name", 1, "text"],
  };

  function value(row, attribute, kind) {
    var raw = row.dataset[attribute];
    if (raw === undefined || raw === "") return null;
    return kind === "num" ? parseFloat(raw) : raw.toLowerCase();
  }

  function apply(order) {
    var spec = ORDERS[order];
    document.querySelectorAll("[data-list]").forEach(function (list) {
      var rows = Array.prototype.slice.call(list.querySelectorAll(":scope > .res"));
      rows.sort(function (a, b) {
        var place = parseFloat(a.dataset.place) - parseFloat(b.dataset.place);
        if (!spec) return place;
        var x = value(a, spec[0], spec[2]), y = value(b, spec[0], spec[2]);
        if (x === null && y === null) return place;
        if (x === null) return 1;
        if (y === null) return -1;
        if (x === y) return place;
        return (x < y ? -1 : 1) * spec[1];
      });
      rows.forEach(function (row) { list.appendChild(row); });
    });
  }

  function remember(order) {
    try {
      var url = new URL(window.location.href);
      if (order && ORDERS[order]) url.searchParams.set("order", order);
      else url.searchParams.delete("order");
      window.history.replaceState(null, "", url);
    } catch (e) {}
  }

  select.addEventListener("change", function () {
    apply(select.value);
    remember(select.value);
  });

  var asked = new URLSearchParams(window.location.search).get("order");
  if (asked && ORDERS[asked] && select.querySelector('option[value="' + asked + '"]')) {
    select.value = asked;
    apply(asked);
  }
})();
