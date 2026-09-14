import { ElementDatum, Graph, IElementEvent } from '@antv/g6';
import isEmpty from 'lodash/isEmpty';
import { useCallback, useEffect, useMemo, useRef } from 'react';
import { buildNodesAndCombos } from './util';

import styles from './index.less';

const TooltipColorMap = {
  combo: 'red',
  node: 'black',
  edge: 'blue',
};

const escapeHtml = (value: unknown) =>
  String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');

const displayList = (value: unknown) =>
  Array.isArray(value) ? value.map(escapeHtml).join(', ') : escapeHtml(value);

interface IProps {
  data: any;
  show: boolean;
}

const ForceGraph = ({ data, show }: IProps) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);

  const nextData = useMemo(() => {
    if (!isEmpty(data)) {
      const graphData = data;
      const mi = buildNodesAndCombos(graphData.nodes);
      return { edges: graphData.edges, ...mi };
    }
    return { nodes: [], edges: [] };
  }, [data]);

  const render = useCallback(() => {
    const graph = new Graph({
      container: containerRef.current!,
      autoFit: 'view',
      autoResize: true,
      behaviors: [
        'drag-element',
        'drag-canvas',
        'zoom-canvas',
        'collapse-expand',
        {
          type: 'hover-activate',
          degree: 1, // 👈🏻 Activate relations.
        },
      ],
      plugins: [
        {
          type: 'tooltip',
          enterable: true,
          getContent: (e: IElementEvent, items: ElementDatum) => {
            if (!Array.isArray(items)) return undefined;
            if (items.some((item) => item?.isCombo)) {
              return `<p style="font-weight:600;color:red">${escapeHtml(items?.[0]?.data?.label)}</p>`;
            }
            let result = '';
            items.forEach((item) => {
              const color =
                TooltipColorMap[
                  e['targetType'] as keyof typeof TooltipColorMap
                ] || 'black';
              result += `<section style="color:${color};max-width:420px;white-space:normal"><h3>${escapeHtml(item?.id)}</h3>`;
              if (item?.entity_type) {
                result += `<div style="padding-bottom:6px"><b>Entity type: </b>${escapeHtml(item.entity_type)}</div>`;
              }
              if (item?.relation_types) {
                result += `<div style="padding-bottom:6px"><b>Relations: </b>${displayList(item.relation_types)}</div>`;
              }
              if (item?.weight) {
                result += `<div><b>Weight: </b>${escapeHtml(item.weight)}</div>`;
              }
              if (item?.description) {
                result += `<p>${escapeHtml(item.description)}</p>`;
              }
              if (item?.textbook_source_ids) {
                result += `<div><b>Textbook source: </b>${displayList(item.textbook_source_ids)}</div>`;
              }
              result += '</section>';
            });
            return result;
          },
        },
      ],
      layout: {
        type: 'combo-combined',
        preventOverlap: true,
        comboPadding: 1,
        spacing: 100,
      },
      node: {
        style: {
          size: 150,
          labelText: (d) => d.id,
          // labelPadding: 30,
          labelFontSize: 40,
          //   labelOffsetX: 20,
          labelOffsetY: 20,
          labelPlacement: 'center',
          labelWordWrap: true,
        },
        palette: {
          type: 'group',
          field: (d) => {
            return d?.entity_type as string;
          },
        },
      },
      edge: {
        style: (model) => {
          const weight: number = Number(model?.weight) || 2;
          const lineWeight = weight * 4;
          return {
            stroke: '#99ADD1',
            lineWidth: lineWeight > 10 ? 10 : lineWeight,
          };
        },
      },
    });

    if (graphRef.current) {
      graphRef.current.destroy();
    }

    graphRef.current = graph;

    graph.setData(nextData);

    graph.render();
  }, [nextData]);

  useEffect(() => {
    if (!isEmpty(data)) {
      render();
    }
  }, [data, render]);

  return (
    <div
      ref={containerRef}
      className={styles.forceContainer}
      style={{
        width: '100%',
        height: '100%',
        display: show ? 'block' : 'none',
      }}
    />
  );
};

export default ForceGraph;
