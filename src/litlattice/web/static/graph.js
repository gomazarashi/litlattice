(function () {
  'use strict';

  var UUID_PATTERN = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
  var CYTOSCAPE_MISSING_MESSAGE =
    'グラフを表示できませんでした（Cytoscape.js を読み込めません）。下の一覧をご覧ください。';
  var ROWS_PER_COLUMN = 30;
  var COLUMN_STEP = 260;
  var SUB_COLUMN_STEP = 200;
  var ROW_STEP = 56;
  var FIT_PADDING = 40;
  // Fitting a small graph would otherwise zoom in until labels overlap.
  var MAX_FIT_ZOOM = 1;
  // cose (the only force-directed layout in Cytoscape's core) costs about
  // O(n^2) per iteration: in Chrome, 1000 Papers took 3.5 s for 100
  // iterations and 2000 Papers 15 s. Larger Libraries use a concentric
  // layout ordered by PageRank instead (2000 Papers in under 0.1 s).
  var FORCE_LAYOUT_MAX_NODES = 500;
  var FORCE_LAYOUT_ITERATIONS = 250;

  var cy = null;
  var filterQuery = '';
  var filterMatchCount = 0;
  var normalStatusText = '';

  var GRAPH_STYLE = [
    {
      selector: 'node',
      style: {
        'shape': 'ellipse',
        'width': 'data(size)',
        'height': 'data(size)',
        'background-color': '#ffffff',
        'border-width': 2,
        'border-style': 'dashed',
        'border-color': '#5d6975',
        'label': 'data(label)',
        'color': '#1d232a',
        'font-size': 12,
        'text-valign': 'bottom',
        'text-halign': 'center',
        'text-margin-y': 4,
        'text-outline-color': '#ffffff',
        'text-outline-width': 2,
        'min-zoomed-font-size': 6
      }
    },
    {
      selector: 'node[?inLibrary]',
      style: {
        'background-color': '#1f5f8b',
        'border-style': 'solid',
        'border-color': '#1f5f8b'
      }
    },
    {
      selector: 'node[?isSeed]',
      style: {
        'shape': 'diamond',
        'border-width': 4,
        'border-style': 'solid',
        'border-color': '#1f5f8b'
      }
    },
    {
      selector: 'node.hover',
      style: {
        'border-style': 'solid',
        'border-color': '#1d232a',
        'border-width': 4
      }
    },
    {
      selector: 'node.match',
      style: {
        'border-color': '#c26a00',
        'border-width': 4
      }
    },
    {
      selector: 'node.dim',
      style: { 'opacity': 0.15 }
    },
    {
      selector: 'edge',
      style: {
        'width': 1.2,
        'line-color': '#7b8794',
        'target-arrow-color': '#7b8794',
        'target-arrow-shape': 'triangle',
        'arrow-scale': 0.8,
        'curve-style': 'bezier'
      }
    },
    {
      selector: 'edge.dim',
      style: { 'opacity': 0.15 }
    }
  ];

  function byId(id) {
    return document.getElementById(id);
  }

  function setStatus(message) {
    var status = byId('graph-status');
    if (status) {
      status.textContent = message;
    }
  }

  function errorMessage(error) {
    return error && error.message ? String(error.message) : String(error);
  }

  function nodeLabel(paper) {
    if (paper.title) {
      return paper.title.length > 40 ? paper.title.slice(0, 40) + '…' : paper.title;
    }
    var identifiers = paper.identifiers || [];
    if (identifiers.length) {
      return identifiers[0].scheme + ':' + identifiers[0].normalized_value;
    }
    return String(paper.id).slice(0, 8);
  }

  function identifierText(paper) {
    var identifiers = paper.identifiers || [];
    var parts = [];
    for (var i = 0; i < identifiers.length; i++) {
      parts.push(identifiers[i].scheme + ':' + identifiers[i].value);
    }
    return parts.join(' ');
  }

  function libraryNodeSize(pagerank, count) {
    var value = Number(pagerank);
    if (!isFinite(value) || value < 0) {
      value = 0;
    }
    var score = Math.sqrt(value * count);
    return Math.max(16, Math.min(56, 16 + 20 * score));
  }

  /*
   * 周辺ビューの配置規則:
   * 1. 返された edges だけを使い、seed から outgoing（引用先）を辿った距離 outDist と、
   *    incoming（被引用元）を辿った距離 inDist を幅優先で求める。
   * 2. side の決定: outDist だけ → references（右）、inDist だけ → citations（左）、
   *    両方 → 小さい方（同じなら both）。どちらも無い場合は、隣接する決定済み node の
   *    うち distance が最小（同点は node の並び順で先）の side を継ぐ。無ければ both。
   * 3. 列: seed は x=0。references は x=+distance*260、citations は x=-distance*260、
   *    both は x=0。
   * 4. 列の中は API の nodes の順に 56 間隔。references / citations は列ごとに中央揃え、
   *    both は seed の下（y=80 から）。1列 30 個を超えたら、references は右・citations は
   *    左・both は左右交互に、幅 200 のサブ列へ折り返す。
   */
  function computeNeighborhoodPositions(nodes, edges, seedId) {
    var order = {};
    var nodeById = {};
    var ids = [];
    var i;
    for (i = 0; i < nodes.length; i++) {
      var nodeId = String(nodes[i].paper.id);
      order[nodeId] = i;
      nodeById[nodeId] = nodes[i];
      ids.push(nodeId);
    }

    var outAdj = {};
    var inAdj = {};
    var adjacent = {};
    function add(map, from, to) {
      if (!map[from]) {
        map[from] = [];
      }
      map[from].push(to);
    }
    for (i = 0; i < edges.length; i++) {
      var citing = String(edges[i].citing_paper_id);
      var cited = String(edges[i].cited_paper_id);
      add(outAdj, citing, cited);
      add(inAdj, cited, citing);
      add(adjacent, citing, cited);
      add(adjacent, cited, citing);
    }

    function bfs(start, adjacency) {
      var dist = {};
      if (!start || !(start in nodeById)) {
        return dist;
      }
      dist[start] = 0;
      var queue = [start];
      var head = 0;
      while (head < queue.length) {
        var current = queue[head++];
        var next = adjacency[current] || [];
        for (var j = 0; j < next.length; j++) {
          if (!(next[j] in dist)) {
            dist[next[j]] = dist[current] + 1;
            queue.push(next[j]);
          }
        }
      }
      return dist;
    }

    var outDist = bfs(seedId, outAdj);
    var inDist = bfs(seedId, inAdj);

    var side = {};
    for (i = 0; i < ids.length; i++) {
      var id = ids[i];
      var hasOut = id in outDist;
      var hasIn = id in inDist;
      if (hasOut && !hasIn) {
        side[id] = 'references';
      } else if (hasIn && !hasOut) {
        side[id] = 'citations';
      } else if (hasOut && hasIn) {
        if (outDist[id] < inDist[id]) {
          side[id] = 'references';
        } else if (inDist[id] < outDist[id]) {
          side[id] = 'citations';
        } else {
          side[id] = 'both';
        }
      } else {
        side[id] = null;
      }
    }

    var changed = true;
    while (changed) {
      changed = false;
      for (i = 0; i < ids.length; i++) {
        id = ids[i];
        if (side[id] !== null) {
          continue;
        }
        var bestId = null;
        var around = adjacent[id] || [];
        for (var k = 0; k < around.length; k++) {
          var candidate = around[k];
          if (side[candidate] === null || side[candidate] === undefined) {
            continue;
          }
          if (
            bestId === null ||
            Number(nodeById[candidate].distance) < Number(nodeById[bestId].distance) ||
            (Number(nodeById[candidate].distance) === Number(nodeById[bestId].distance) &&
              order[candidate] < order[bestId])
          ) {
            bestId = candidate;
          }
        }
        if (bestId !== null) {
          side[id] = side[bestId];
          changed = true;
        }
      }
    }
    for (i = 0; i < ids.length; i++) {
      if (side[ids[i]] === null || side[ids[i]] === undefined) {
        side[ids[i]] = 'both';
      }
    }

    var columns = [];
    var columnIndex = {};
    for (i = 0; i < ids.length; i++) {
      id = ids[i];
      if (id === seedId) {
        continue;
      }
      var distance = Number(nodeById[id].distance) || 0;
      var key = side[id] + '|' + distance;
      if (!(key in columnIndex)) {
        columnIndex[key] = columns.length;
        columns.push({ side: side[id], distance: distance, ids: [] });
      }
      columns[columnIndex[key]].ids.push(id);
    }

    var positions = {};
    positions[seedId] = { x: 0, y: 0 };
    for (var c = 0; c < columns.length; c++) {
      var column = columns[c];
      for (var chunk = 0; chunk * ROWS_PER_COLUMN < column.ids.length; chunk++) {
        var chunkIds = column.ids.slice(chunk * ROWS_PER_COLUMN, (chunk + 1) * ROWS_PER_COLUMN);
        var x;
        if (column.side === 'references') {
          x = column.distance * COLUMN_STEP + chunk * SUB_COLUMN_STEP;
        } else if (column.side === 'citations') {
          x = -column.distance * COLUMN_STEP - chunk * SUB_COLUMN_STEP;
        } else if (chunk === 0) {
          x = 0;
        } else if (chunk % 2 === 1) {
          x = ((chunk + 1) / 2) * SUB_COLUMN_STEP;
        } else {
          x = -(chunk / 2) * SUB_COLUMN_STEP;
        }
        for (var r = 0; r < chunkIds.length; r++) {
          var y = column.side === 'both'
            ? 80 + r * ROW_STEP
            : (r - (chunkIds.length - 1) / 2) * ROW_STEP;
          positions[chunkIds[r]] = { x: x, y: y };
        }
      }
    }
    return positions;
  }

  function buildElements(nodes, edges, isNeighborhood, seedId) {
    var elements = [];
    var positions = isNeighborhood ? computeNeighborhoodPositions(nodes, edges, seedId) : null;
    var gridColumns = Math.max(1, Math.ceil(Math.sqrt(nodes.length)));
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      var paper = node.paper;
      var id = String(paper.id);
      var isSeed = isNeighborhood && seedId !== null && id === seedId;
      elements.push({
        data: {
          id: id,
          label: nodeLabel(paper),
          title: paper.title || '',
          identifiers: identifierText(paper),
          inLibrary: paper.in_library === true,
          isSeed: isSeed,
          pagerank: Number(node.pagerank) || 0,
          inDegree: Number(node.in_degree) || 0,
          outDegree: Number(node.out_degree) || 0,
          distance: Number(node.distance) || 0,
          size: isNeighborhood
            ? (isSeed ? 40 : 22)
            : libraryNodeSize(node.pagerank, nodes.length)
        },
        position: positions
          ? positions[id] || { x: 0, y: 0 }
          : { x: (i % gridColumns) * 60, y: Math.floor(i / gridColumns) * 60 }
      });
    }
    for (var e = 0; e < edges.length; e++) {
      elements.push({
        data: {
          id: 'e' + e,
          source: String(edges[e].citing_paper_id),
          target: String(edges[e].cited_paper_id)
        }
      });
    }
    return elements;
  }

  function restoreStatus() {
    if (filterQuery) {
      setStatus(
        filterMatchCount
          ? filterMatchCount + ' 件の論文が一致しました'
          : '一致する論文はありません'
      );
    } else {
      setStatus(normalStatusText);
    }
  }

  function applyFilter(raw) {
    if (!cy) {
      return;
    }
    filterQuery = raw.toLowerCase().replace(/\s+/g, '');
    if (!filterQuery) {
      cy.elements().removeClass('dim');
      cy.nodes().removeClass('match');
      filterMatchCount = 0;
      restoreStatus();
      return;
    }
    var matched = cy.nodes().filter(function (node) {
      var haystack = (
        node.data('title') + ' ' + node.data('identifiers') + ' ' + node.data('id')
      )
        .toLowerCase()
        .replace(/\s+/g, '');
      return haystack.indexOf(filterQuery) !== -1;
    });
    filterMatchCount = matched.length;
    cy.nodes().removeClass('match');
    cy.nodes().not(matched).addClass('dim');
    cy.edges().addClass('dim');
    matched.removeClass('dim').addClass('match');
    restoreStatus();
  }

  function fitView(elements) {
    var target = elements && elements.length ? elements : cy.elements();
    cy.fit(target, FIT_PADDING);
    if (cy.zoom() > MAX_FIT_ZOOM) {
      cy.zoom(MAX_FIT_ZOOM);
      cy.center(target);
    }
  }

  function handleAction(event) {
    if (!cy) {
      return;
    }
    var action = event.currentTarget.getAttribute('data-graph-action');
    var container = cy.container();
    var center = container
      ? { x: container.clientWidth / 2, y: container.clientHeight / 2 }
      : { x: 0, y: 0 };
    if (action === 'zoom-in') {
      cy.zoom({ level: cy.zoom() * 1.25, renderedPosition: center });
    } else if (action === 'zoom-out') {
      cy.zoom({ level: cy.zoom() * 0.8, renderedPosition: center });
    } else if (action === 'fit') {
      fitView();
    }
  }

  function bindInteractions(canvas) {
    var actions = document.querySelectorAll('[data-graph-action]');
    for (var i = 0; i < actions.length; i++) {
      actions[i].addEventListener('click', handleAction);
    }

    var filter = byId('graph-filter');
    if (filter) {
      filter.addEventListener('input', function () {
        applyFilter(filter.value);
      });
      filter.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter' || !filterQuery) {
          return;
        }
        var matched = cy.nodes('.match');
        if (matched.length) {
          fitView(matched);
        }
      });
    }

    cy.on('tap', 'node', function (event) {
      var template = canvas.getAttribute('data-paper-url-template');
      if (!template) {
        return;
      }
      var id = event.target.data('id');
      window.location.href = template.replace(UUID_PATTERN, encodeURIComponent(id));
    });

    cy.on('mouseover', 'node', function (event) {
      var node = event.target;
      node.addClass('hover');
      var name = node.data('title') || node.data('label');
      setStatus(
        name + '（被引用 ' + node.data('inDegree') +
        '・引用 ' + node.data('outDegree') +
        '・PageRank ' + Number(node.data('pagerank')).toFixed(4) + '）'
      );
    });

    cy.on('mouseout', 'node', function (event) {
      event.target.removeClass('hover');
      restoreStatus();
    });
  }

  function layoutOptions(isNeighborhood, useForceLayout) {
    if (isNeighborhood) {
      return { name: 'preset', animate: false, fit: false, padding: FIT_PADDING };
    }
    if (useForceLayout) {
      return {
        name: 'cose',
        animate: false,
        randomize: false,
        fit: false,
        padding: FIT_PADDING,
        numIter: FORCE_LAYOUT_ITERATIONS,
        nodeOverlap: 8,
        idealEdgeLength: function () { return 140; },
        nodeRepulsion: function () { return 40000; }
      };
    }
    var ranks = cy.nodes().map(function (node) { return node.data('pagerank'); });
    var spread = Math.max.apply(null, ranks) - Math.min.apply(null, ranks);
    return {
      name: 'concentric',
      animate: false,
      fit: false,
      padding: FIT_PADDING,
      minNodeSpacing: 8,
      concentric: function (node) { return node.data('pagerank'); },
      // About ten rings from the highest PageRank in the centre outwards.
      levelWidth: function () { return spread > 0 ? spread / 10 : 1; }
    };
  }

  function render(canvas, data) {
    var nodes = data.nodes || [];
    var edges = data.edges || [];
    if (!nodes.length) {
      setStatus('表示する論文がありません。');
      return;
    }
    var kind = data.kind || canvas.getAttribute('data-graph-kind') || 'library';
    var isNeighborhood = kind === 'neighborhood';
    var seedId = data.seed_paper_id
      ? String(data.seed_paper_id)
      : canvas.getAttribute('data-seed-id');
    var useForceLayout = !isNeighborhood && nodes.length <= FORCE_LAYOUT_MAX_NODES;
    normalStatusText =
      '論文 ' + nodes.length + ' 件・引用 ' + edges.length + ' 件。' +
      (!isNeighborhood && !useForceLayout
        ? '論文が多いため、PageRank の高い論文ほど中心に来る同心円状に配置しています。'
        : '') +
      'ホイールで拡大縮小、ドラッグで移動、クリックで詳細を開きます。';
    filterQuery = '';
    filterMatchCount = 0;

    cy = window.cytoscape({
      container: canvas,
      elements: buildElements(nodes, edges, isNeighborhood, seedId),
      style: GRAPH_STYLE,
      // Keep the positions given in the elements; without this Cytoscape
      // runs its default grid layout on init and discards them.
      layout: { name: 'preset' },
      minZoom: 0.05,
      maxZoom: 8
    });

    bindInteractions(canvas);

    var layout = cy.layout(layoutOptions(isNeighborhood, useForceLayout));
    layout.on('layoutstop', function () {
      cy.resize();
      fitView();
      restoreStatus();
    });
    layout.run();
  }

  function loadGraph(canvas) {
    var url = canvas.getAttribute('data-api-url');
    setStatus('読み込み中…');
    if (!url) {
      setStatus('グラフを読み込めませんでした: API URL が指定されていません');
      return;
    }
    fetch(url, { headers: { Accept: 'application/json' } })
      .then(function (response) {
        return response
          .json()
          .catch(function () {
            return null;
          })
          .then(function (payload) {
            if (!response.ok) {
              var message = payload && payload.error && payload.error.message
                ? payload.error.message
                : 'HTTP ' + response.status;
              throw new Error(message);
            }
            return payload;
          });
      })
      .then(function (payload) {
        if (!payload || payload.ok !== true || !payload.data) {
          var message = payload && payload.error && payload.error.message
            ? payload.error.message
            : '応答を解釈できませんでした';
          throw new Error(message);
        }
        try {
          render(canvas, payload.data);
        } catch (error) {
          setStatus('グラフを表示できませんでした: ' + errorMessage(error));
        }
      })
      .catch(function (error) {
        setStatus('グラフを読み込めませんでした: ' + errorMessage(error));
      });
  }

  function onReady() {
    var canvas = byId('graph-canvas');
    if (!canvas) {
      return;
    }
    if (!window.cytoscape) {
      setStatus(CYTOSCAPE_MISSING_MESSAGE);
      return;
    }
    loadGraph(canvas);
  }

  document.addEventListener('DOMContentLoaded', onReady);
})();
