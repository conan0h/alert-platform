package fleet

import (
	"fmt"
	"sort"
	"strconv"
)

// Effective is the fully resolved configuration for one service: fleet
// defaults deep-merged with the service spec, per the semantics fixed in
// docs/spec.md — scalars replace, maps merge key by key, lists replace
// wholesale.
//
// Everything the platform renders (the unit file, the injected environment,
// the scrape target) comes from here, so "what will actually run" is one
// function call and one printable structure rather than an inference across
// two files.
type Effective struct {
	Name        string
	Tier        string
	Description string
	Config      map[string]any
}

// Resolve merges fleet defaults with a service spec.
func Resolve(f Fleet, s Service) Effective {
	merged := deepMerge(f.Defaults, s.Spec)
	tier := s.Metadata.Tier
	if tier == "" {
		tier = "standard"
	}
	return Effective{
		Name:        s.Metadata.Name,
		Tier:        tier,
		Description: s.Metadata.Description,
		Config:      merged,
	}
}

// deepMerge returns base overlaid with over.
//
// Lists are replaced, never concatenated. That was a deliberate spec
// decision: concatenation makes the effective value impossible to determine
// from reading one file, which turns "where did this element come from?"
// into an incident-time question.
func deepMerge(base, over map[string]any) map[string]any {
	out := make(map[string]any, len(base)+len(over))
	for k, v := range base {
		out[k] = cloneValue(v)
	}
	for k, v := range over {
		if bv, ok := out[k]; ok {
			bm, bIsMap := toMap(bv)
			om, oIsMap := toMap(v)
			if bIsMap && oIsMap {
				out[k] = deepMerge(bm, om)
				continue
			}
		}
		out[k] = cloneValue(v)
	}
	return out
}

func toMap(v any) (map[string]any, bool) {
	switch m := v.(type) {
	case map[string]any:
		return m, true
	case map[any]any: // some YAML paths produce this shape
		out := make(map[string]any, len(m))
		for k, val := range m {
			out[fmt.Sprint(k)] = val
		}
		return out, true
	}
	return nil, false
}

func cloneValue(v any) any {
	if m, ok := toMap(v); ok {
		return deepMerge(map[string]any{}, m)
	}
	if l, ok := v.([]any); ok {
		out := make([]any, len(l))
		for i, e := range l {
			out[i] = cloneValue(e)
		}
		return out
	}
	return v
}

// -- typed accessors -------------------------------------------------------

// Get walks a dotted path, e.g. "health.metrics.port".
func (e Effective) Get(path string) (any, bool) {
	cur := any(e.Config)
	for _, part := range splitPath(path) {
		m, ok := toMap(cur)
		if !ok {
			return nil, false
		}
		cur, ok = m[part]
		if !ok {
			return nil, false
		}
	}
	return cur, true
}

func (e Effective) String(path, fallback string) string {
	v, ok := e.Get(path)
	if !ok || v == nil {
		return fallback
	}
	return fmt.Sprint(v)
}

func (e Effective) Int(path string, fallback int) int {
	v, ok := e.Get(path)
	if !ok {
		return fallback
	}
	switch n := v.(type) {
	case int:
		return n
	case int64:
		return int(n)
	case float64:
		return int(n)
	case uint64:
		return int(n)
	case string:
		if parsed, err := strconv.Atoi(n); err == nil {
			return parsed
		}
	}
	return fallback
}

func (e Effective) Bool(path string, fallback bool) bool {
	v, ok := e.Get(path)
	if !ok {
		return fallback
	}
	if b, ok := v.(bool); ok {
		return b
	}
	if s, ok := v.(string); ok {
		parsed, err := strconv.ParseBool(s)
		if err == nil {
			return parsed
		}
	}
	return fallback
}

// Map returns a nested map at a dotted path, with sorted keys available via
// SortedKeys. Used for spec.polling, whose keys are service-defined.
func (e Effective) Map(path string) map[string]any {
	v, ok := e.Get(path)
	if !ok {
		return nil
	}
	m, _ := toMap(v)
	return m
}

func SortedKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func splitPath(path string) []string {
	var parts []string
	cur := ""
	for _, r := range path {
		if r == '.' {
			parts = append(parts, cur)
			cur = ""
			continue
		}
		cur += string(r)
	}
	if cur != "" {
		parts = append(parts, cur)
	}
	return parts
}

// SecretNames returns every secret this service references, by spec name.
//
// Any key ending in `_secret` anywhere in the effective config is a secret
// reference — the same convention the schema enforces. Discovering them
// rather than listing them means adding a secret to a spec needs no change
// to the apply engine.
func (e Effective) SecretNames() []string {
	found := map[string]bool{}
	var walk func(any)
	walk = func(v any) {
		m, ok := toMap(v)
		if !ok {
			if l, ok := v.([]any); ok {
				for _, e := range l {
					walk(e)
				}
			}
			return
		}
		for k, val := range m {
			if len(k) > 7 && k[len(k)-7:] == "_secret" {
				if s, ok := val.(string); ok && s != "" {
					found[s] = true
				}
				continue
			}
			walk(val)
		}
	}
	walk(e.Config)

	out := make([]string, 0, len(found))
	for name := range found {
		out = append(out, name)
	}
	sort.Strings(out)
	return out
}
