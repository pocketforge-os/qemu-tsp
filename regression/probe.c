// probe.c — deterministic evdev ioctl probe. Records every line, never aborts.
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <errno.h>
#include <sys/ioctl.h>
#include <linux/input.h>
static void dh(const char*l,const unsigned char*b,int n){printf("%s:",l);for(int i=0;i<n;i++)printf("%02x",b[i]);printf("\n");}
int main(int c,char**v){
  const char*d=c>1?v[1]:"/dev/input/event0";
  printf("SIZEOF input_event=%zu input_absinfo=%zu input_id=%zu\n",sizeof(struct input_event),sizeof(struct input_absinfo),sizeof(struct input_id));
  int fd=open(d,O_RDONLY); if(fd<0){printf("OPEN_FAIL %s errno=%d %s\n",d,errno,strerror(errno));return 2;}
  struct input_id id; if(ioctl(fd,EVIOCGID,&id)==0)printf("EVIOCGID bus=%04x ven=%04x prod=%04x ver=%04x\n",id.bustype,id.vendor,id.product,id.version);else printf("EVIOCGID FAIL errno=%d %s\n",errno,strerror(errno));
  char nm[256]={0}; if(ioctl(fd,EVIOCGNAME(sizeof nm),nm)>=0)printf("EVIOCGNAME %s\n",nm);else printf("EVIOCGNAME FAIL errno=%d %s\n",errno,strerror(errno));
  unsigned char eb[(EV_MAX/8)+1]; memset(eb,0,sizeof eb); if(ioctl(fd,EVIOCGBIT(0,sizeof eb),eb)>=0)dh("EVIOCGBIT0",eb,sizeof eb);else printf("EVIOCGBIT0 FAIL errno=%d %s\n",errno,strerror(errno));
  unsigned char kb[(KEY_MAX/8)+1]; memset(kb,0,sizeof kb); if(ioctl(fd,EVIOCGBIT(EV_KEY,sizeof kb),kb)>=0)dh("EVIOCGBIT_KEY",kb,sizeof kb);else printf("EVIOCGBIT_KEY FAIL errno=%d %s\n",errno,strerror(errno));
  unsigned char ab[(ABS_MAX/8)+1]; memset(ab,0,sizeof ab); if(ioctl(fd,EVIOCGBIT(EV_ABS,sizeof ab),ab)>=0)dh("EVIOCGBIT_ABS",ab,sizeof ab);else printf("EVIOCGBIT_ABS FAIL errno=%d %s\n",errno,strerror(errno));
  int ax[]={ABS_X,ABS_Y,ABS_Z,ABS_RZ,ABS_HAT0X,ABS_HAT0Y}; const char*an[]={"ABS_X","ABS_Y","ABS_Z","ABS_RZ","ABS_HAT0X","ABS_HAT0Y"};
  for(int i=0;i<6;i++){struct input_absinfo a;memset(&a,0,sizeof a);if(ioctl(fd,EVIOCGABS(ax[i]),&a)==0)printf("EVIOCGABS_%s val=%d min=%d max=%d fuzz=%d flat=%d res=%d\n",an[i],a.value,a.minimum,a.maximum,a.fuzz,a.flat,a.resolution);else printf("EVIOCGABS_%s FAIL errno=%d %s\n",an[i],errno,strerror(errno));}
  close(fd); return 0;
}
