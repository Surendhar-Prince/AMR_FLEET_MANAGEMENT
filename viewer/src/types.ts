export interface MapNode {
  id: string;
  x: number;
  y: number;
}

export interface MapEdge {
  from: string;
  to: string;
}

export interface MapData {
  nodes: MapNode[];
  edges: MapEdge[];
  amr_width: number;
  amr_length: number;
  amr_speed: number;
}

export interface AmrState {
  id: string;
  position: { x: number; y: number };
  heading: number;
  path: string[];
  colliding: boolean;
}
