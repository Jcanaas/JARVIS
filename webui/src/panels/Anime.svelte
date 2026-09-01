<script>
  import StreamingBrowser from '../lib/StreamingBrowser.svelte'
  let { active = true } = $props()
</script>

<StreamingBrowser kind="anime" label="Anime" {active} />
