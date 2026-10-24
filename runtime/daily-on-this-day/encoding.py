"""Encoding argument selection; CPU quality parity has not been established."""
def command_for_backend(args,backend):
 if backend not in ('nvenc','cpu'):raise ValueError('Unknown encoding backend')
 args=list(args)
 if backend=='cpu' and 'h264_nvenc' in args:
  args[args.index('h264_nvenc')]='libx264'
  if '-preset' in args:args[args.index('-preset')+1]='veryfast'
  for flag in ('-rc','-cq'):
   while flag in args:
    i=args.index(flag);del args[i:i+2]
  args[-1:-1]=['-crf','20']
 return args
