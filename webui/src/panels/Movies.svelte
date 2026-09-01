<script>
  import StreamingBrowser from '../lib/StreamingBrowser.svelte'
  let { active = true } = $props()
</script>

<StreamingBrowser kind="movie" label="Películas" {active} />
